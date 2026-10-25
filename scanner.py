
#!/usr/bin/env python3
"""HVLK AI — US stock market scanner."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
RESULTS_FILE = DATA_DIR / "results.json"
MARKET_FILE = DATA_DIR / "market.json"

WORKERS = max(
    1, min(int(os.getenv("HVLK_WORKERS", "3")), 8)
)
MIN_REQUEST_INTERVAL = max(
    0.5, float(os.getenv("HVLK_MIN_REQUEST_INTERVAL", "1.5"))
)
SAVE_EVERY = max(1, int(os.getenv("HVLK_SAVE_EVERY", "50")))
TOP_COUNT = max(
    1, min(int(os.getenv("HVLK_TOP_COUNT", "25")), 100)
)
REQUEST_TIMEOUT = max(
    5, int(os.getenv("HVLK_REQUEST_TIMEOUT", "20"))
)
MAX_RETRIES = max(
    0, min(int(os.getenv("HVLK_MAX_RETRIES", "3")), 6)
)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_rate_lock = threading.Lock()
_last_request_at = 0.0

_rate_limit_lock = threading.Lock()
_rate_limit_events = 0
_abort_event = threading.Event()


# ============================================================
# UTILITIES
# ============================================================

def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically to avoid truncated files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary_path, path)


def normalise_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    symbol = value.strip().upper().replace(".", "-")

    if not symbol or len(symbol) > 32:
        return None

    if not re.fullmatch(r"[A-Z0-9^_-]+", symbol):
        return None

    return symbol


def safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if not (
        number == number
        and number not in (float("inf"), float("-inf"))
    ):
        return None

    return number


# ============================================================
# LOAD STOCK UNIVERSE
# ============================================================

def load_universe() -> list[dict[str, Any]]:
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            f"Missing universe file: {UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        candidates = raw.get(
            "symbols",
            raw.get("universe", raw.get("instruments", [])),
        )
    else:
        raise ValueError(
            "universe.json must contain a list or an object "
            "with a symbols list."
        )

    if not isinstance(candidates, list):
        raise ValueError("Universe symbols must be a JSON list.")

    instruments: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in candidates:
        if isinstance(entry, str):
            symbol = normalise_symbol(entry)
            item: dict[str, Any] = {
                "symbol": symbol,
                "name": symbol,
            }
        elif isinstance(entry, dict):
            symbol = normalise_symbol(
                entry.get("symbol") or entry.get("ticker")
            )
            item = dict(entry)
            item["symbol"] = symbol
            item["name"] = str(
                entry.get("name") or symbol or "Unknown"
            )[:200]
        else:
            continue

        if not symbol or symbol in seen:
            continue

        seen.add(symbol)
        item["symbol"] = symbol
        item["name"] = str(item.get("name") or symbol)[:200]
        instruments.append(item)

    if not instruments:
        raise ValueError(
            "No valid symbols found in data/universe.json."
        )

    return instruments


# ============================================================
# REQUEST RATE LIMITING
# ============================================================

def throttle() -> None:
    """Enforce a minimum interval between request starts."""
    global _last_request_at

    with _rate_lock:
        current = time.monotonic()
        wait_time = MIN_REQUEST_INTERVAL - (
            current - _last_request_at
        )

        if wait_time > 0:
            time.sleep(wait_time)

        _last_request_at = time.monotonic()


def read_limited(response: Any) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0

    while True:
        remaining = MAX_RESPONSE_BYTES + 1 - total_bytes
        chunk = response.read(min(65536, remaining))

        if not chunk:
            break

        chunks.append(chunk)
        total_bytes += len(chunk)

        if total_bytes > MAX_RESPONSE_BYTES:
            raise ValueError(
                "Yahoo Finance response exceeded size limit."
            )

    return b"".join(chunks)


# ============================================================
# YAHOO FINANCE API
# ============================================================

def fetch_chart(symbol: str) -> dict[str, Any]:
    global _rate_limit_events

    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded_symbol}"
        "?range=1y&interval=1d&events=div%2Csplits"
    )

    last_error = "Unknown error"

    for attempt in range(MAX_RETRIES + 1):
        if _abort_event.is_set():
            raise RuntimeError(
                "Scan stopped after repeated HTTP 429 responses."
            )

        throttle()

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; HVLK-AI/1.0)"
                ),
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                body = read_limited(response)

            payload = json.loads(body.decode("utf-8"))
            chart = payload.get("chart") or {}

            if chart.get("error"):
                error = chart["error"]
                raise ValueError(
                    "Yahoo chart error: "
                    f"{error.get('code', 'unknown')}: "
                    f"{error.get('description', '')}"
                )

            results = chart.get("result") or []

            if not results:
                raise ValueError(
                    "Yahoo Finance returned no chart data."
                )

            return results[0]

        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"

            if exc.code == 429:
                with _rate_limit_lock:
                    _rate_limit_events += 1
                    event_count = _rate_limit_events

                # Stop rather than repeatedly hammering the provider.
                if event_count >= 3:
                    _abort_event.set()
                    raise RuntimeError(
                        "Repeated HTTP 429; scan stopped."
                    ) from exc

                retry_after = (
                    exc.headers.get("Retry-After")
                    if exc.headers
                    else None
                )

                try:
                    delay = (
                        min(300.0, max(30.0, float(retry_after)))
                        if retry_after
                        else min(180.0, 30.0 * (attempt + 1))
                    )
                except (TypeError, ValueError):
                    delay = min(180.0, 30.0 * (attempt + 1))

            elif exc.code in (
                408, 425, 500, 502, 503, 504
            ) and attempt < MAX_RETRIES:
                delay = min(
                    30.0, 2.0 ** attempt + random.random()
                )

            else:
                raise RuntimeError(last_error) from exc

        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
        ) as exc:
            last_error = f"{type(exc).__name__}: {exc}"

            if attempt >= MAX_RETRIES:
                raise RuntimeError(last_error) from exc

            delay = min(
                20.0, 2.0 ** attempt + random.random()
            )

        if attempt < MAX_RETRIES:
            time.sleep(delay)

    raise RuntimeError(last_error)


# ============================================================
# PARSE STOCK DATA
# ============================================================

def fetch_quote(
    instrument: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    symbol = str(instrument.get("symbol", ""))

    try:
        chart = fetch_chart(symbol)

        meta = chart.get("meta") or {}
        timestamps = chart.get("timestamp") or []
        indicators = chart.get("indicators") or {}
        quote_list = indicators.get("quote") or []

        if not quote_list:
            raise ValueError("Missing quote indicators.")

        quote = quote_list[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        daily_data: list[tuple[int, float, int]] = []

        for index, timestamp in enumerate(timestamps):
            close = safe_float(
                closes[index] if index < len(closes) else None
            )
            volume = safe_float(
                volumes[index] if index < len(volumes) else None
            )

            if close is not None and close > 0:
                daily_data.append(
                    (
                        int(timestamp),
                        close,
                        max(0, int(volume or 0)),
                    )
                )

        if not daily_data:
            raise ValueError("No valid daily closing prices.")

        daily_data.sort(key=lambda row: row[0])

        last_timestamp, last_close, last_volume = daily_data[-1]

        previous_close = (
            daily_data[-2][1] if len(daily_data) > 1 else None
        )

        if previous_close and previous_close > 0:
            change_percent = (
                (last_close - previous_close)
                / previous_close
                * 100
            )
        else:
            change_percent = 0.0

        # Use up to 20 earlier sessions, excluding the latest session.
        previous_volumes = [
            row[2]
            for row in daily_data[-21:-1]
            if row[2] > 0
        ]

        average_volume = (
            sum(previous_volumes) / len(previous_volumes)
            if previous_volumes
            else 0.0
        )

        volume_ratio = (
            last_volume / average_volume
            if average_volume > 0
            else 0.0
        )

        currency = str(meta.get("currency") or "USD")[:12]
        exchange = str(
            meta.get("exchangeName")
            or meta.get("fullExchangeName")
            or ""
        )[:80]

        name = str(
            instrument.get("name")
            or meta.get("longName")
            or meta.get("shortName")
            or symbol
        )[:200]

        dividend_events = (
            (chart.get("events") or {}).get("dividends") or {}
        )

        dividend_amounts: list[float] = []

        for event in dividend_events.values():
            if not isinstance(event, dict):
                continue

            amount = safe_float(event.get("amount"))

            if amount is not None and amount > 0:
                dividend_amounts.append(amount)

        # Historical dividends are not a forward yield forecast.
        dividend_yield = (
            sum(dividend_amounts[-4:]) / last_close * 100
            if dividend_amounts and last_close > 0
            else None
        )

        item = {
            "symbol": symbol,
            "name": name,
            "price": round(last_close, 6),
            "previous_close": (
                round(previous_close, 6)
                if previous_close is not None
                else None
            ),
            "change_percent": round(change_percent, 4),
            "volume": last_volume,
            "average_volume_20d": round(average_volume, 2),
            "volume_ratio": round(volume_ratio, 2),
            "turnover_estimate": round(
                last_close * last_volume, 2
            ),
            "currency": currency,
            "exchange": exchange,
            "last_trade_date": datetime.fromtimestamp(
                last_timestamp, timezone.utc
            ).date().isoformat(),
            "quote_timestamp": now_utc(),
            "dividend_yield_estimate_percent": (
                round(dividend_yield, 3)
                if dividend_yield is not None
                else None
            ),
            "asset_type": str(
                instrument.get("asset_type")
                or instrument.get("type")
                or "US equity"
            )[:40],
        }

        return item, None

    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# ============================================================
# MARKET RANKINGS
# ============================================================

def rank(
    rows: list[dict[str, Any]],
    key: str,
    reverse: bool = True,
) -> list[dict[str, Any]]:
    valid_rows = [
        row for row in rows
        if safe_float(row.get(key)) is not None
    ]

    return sorted(
        valid_rows,
        key=lambda row: float(row[key]),
        reverse=reverse,
    )[:TOP_COUNT]


def error_counts(
    errors: list[dict[str, Any]],
) -> dict[str, int]:
    counts: dict[str, int] = {}

    for entry in errors:
        error = str(entry.get("error") or "Unknown error")

        match = re.match(
            r"([A-Za-z_]+Error|HTTP\s+\d+|RuntimeError|ValueError|TimeoutError)",
            error,
        )

        category = (
            match.group(1)
            if match
            else error.split(":", 1)[0][:80]
        )

        counts[category] = counts.get(category, 0) + 1

    return dict(
        sorted(
            counts.items(),
            key=lambda pair: (-pair[1], pair[0]),
        )
    )


def build_market(
    total: int,
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    started: str,
) -> dict[str, Any]:
    gainers = [
        row for row in rows
        if (safe_float(row.get("change_percent")) or 0) > 0
    ]

    losers = [
        row for row in rows
        if (safe_float(row.get("change_percent")) or 0) < 0
    ]

    unusual = [
        dict(row)
        for row in rows
        if (safe_float(row.get("volume_ratio")) or 0) >= 2.0
    ]

    unusual.sort(
        key=lambda row: safe_float(row.get("volume_ratio")) or 0,
        reverse=True,
    )

    return {
        "project": "HVLK AI",
        "generated_at": now_utc(),
        "market": "US",
        "asset_scope": (
            "US-listed common stocks and ADRs where identified; "
            "classification may be imperfect."
        ),
        "data_provider": "Yahoo Finance chart endpoint",
        "universe_symbols": total,
        "scanned_symbols": len(rows) + len(errors),
        "successful_quotes": len(rows),
        "failed_quotes": len(errors),
        "coverage_percent": (
            round(len(rows) / total * 100, 2)
            if total
            else 0
        ),
        "scan_started_at": started,
        "scan_finished_at": now_utc(),
        "top_gainers": rank(
            gainers, "change_percent", True
        ),
        # Correct: losers are ranked from negative-change stocks.
        "top_losers": rank(
            losers, "change_percent", False
        ),
        "highest_volume": rank(rows, "volume", True),
        "highest_turnover": rank(
            rows, "turnover_estimate", True
        ),
        "unusual_volume": unusual[:TOP_COUNT],
        "errors_by_type": error_counts(errors),
        "sample_errors": errors[:50],
        "data_notes": [
            "Daily closing prices, not live intraday quotes.",
            "Daily change compares the latest two available closes.",
            "Turnover is estimated as closing price times daily volume.",
            "Average volume uses up to 20 earlier available sessions.",
            "Dividend data is a rough historical estimate, not a forecast.",
            "HTTP 404 means data was unavailable for the request.",
            "HTTP 429 means the provider is rate-limiting requests.",
        ],
    }


# ============================================================
# SAVE RESULTS
# ============================================================

def save_all(
    total: int,
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    started: str,
) -> None:
    write_json(
        RESULTS_FILE,
        {
            "project": "HVLK AI",
            "generated_at": now_utc(),
            "market": "US",
            "data_provider": "Yahoo Finance chart endpoint",
            "total_symbols": total,
            "successful_quotes": len(rows),
            "failed_quotes": len(errors),
            "coverage_percent": (
                round(len(rows) / total * 100, 2)
                if total
                else 0
            ),
            "results": rows,
            "errors": errors[:500],
        },
    )

    write_json(
        MARKET_FILE,
        build_market(total, rows, errors, started),
    )


# ============================================================
# MAIN SCAN
# ============================================================

def main() -> None:
    global _rate_limit_events

    started = now_utc()
    instruments = load_universe()
    total = len(instruments)

    _rate_limit_events = 0
    _abort_event.clear()

    print(
        f"HVLK AI: {total} symbols; "
        f"workers={WORKERS}; "
        f"minimum request interval={MIN_REQUEST_INTERVAL}s.",
        flush=True,
    )

    rows_by_symbol: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_quote, instrument): instrument
            for instrument in instruments
        }

        for future in as_completed(futures):
            instrument = futures[future]
            symbol = instrument["symbol"]
            completed += 1

            try:
                row, error = future.result()
            except Exception as exc:
                row = None
                error = f"{type(exc).__name__}: {exc}"

            if row is not None:
                rows_by_symbol[symbol] = row
            else:
                errors.append(
                    {
                        "symbol": symbol,
                        "name": str(
                            instrument.get("name") or symbol
                        )[:200],
                        "error": error or "Unknown error",
                    }
                )

            if completed % 25 == 0 or completed == total:
                print(
                    f"{completed}/{total} | "
                    f"successful={len(rows_by_symbol)} | "
                    f"errors={len(errors)}",
                    flush=True,
                )

            if completed % SAVE_EVERY == 0 or completed == total:
                current_rows = sorted(
                    rows_by_symbol.values(),
                    key=lambda row: row["symbol"],
                )
                save_all(total, current_rows, errors, started)

    current_rows = sorted(
        rows_by_symbol.values(),
        key=lambda row: row["symbol"],
    )

    # Always save final state, including a partial run.
    save_all(total, current_rows, errors, started)
    summary = build_market(total, current_rows, errors, started)

    print("\n--- HVLK AI SUMMARY ---")
    print(f"Successful quotes: {len(current_rows)}")
    print(f"Errors: {len(errors)}")
    print(f"Coverage: {summary['coverage_percent']}%")
    print(f"Errors by type: {summary['errors_by_type']}")

    if _abort_event.is_set():
        raise SystemExit(
            "Scan stopped after repeated HTTP 429. "
            "Results are partial; do not publish them as complete."
        )


if __name__ == "__main__":
    main()
