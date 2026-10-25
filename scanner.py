import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UNIVERSE_FILE = DATA / "universe.json"
RESULTS_FILE = DATA / "results.json"
MARKET_FILE = DATA / "market.json"
WORKERS = 2
TIMEOUT = 20
RETRIES = 3
SAVE_EVERY = 100
TOP_COUNT = 50
USER_AGENT = "HVLK-AI/1.0 personal research"
_cooldown_lock = threading.Lock()
_cooldown_until = 0.0
def now_utc():
    return datetime.now(timezone.utc).isoformat()
def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
def wait_for_cooldown():
    while True:
        with _cooldown_lock:
            remaining = _cooldown_until - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 5))
def extend_cooldown(seconds):
    global _cooldown_until
    with _cooldown_lock:
        _cooldown_until = max(
            _cooldown_until,
            time.monotonic() + seconds,
        )
def load_universe():
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError("Chybí data/universe.json.")
    payload = json.loads(
        UNIVERSE_FILE.read_text(encoding="utf-8")
    )
    items = payload.get("results", [])
    if not isinstance(items, list) or not items:
        raise RuntimeError("Seznam symbolů je prázdný.")
    unique = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol and symbol not in unique:
            row = dict(item)
            row["symbol"] = symbol
            unique[symbol] = row
    return list(unique.values())
def fetch_json(url):
    wait_for_cooldown()
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))
def fetch_quote(instrument):
    symbol = instrument["symbol"]
    yahoo_symbol = symbol.replace(".", "-")
    encoded = urllib.parse.quote(yahoo_symbol, safe="-^=")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded}?range=1y&interval=1d&events=div%2Csplits"
    )
    last_error = "Neznámá chyba"
    for attempt in range(RETRIES + 1):
        try:
            payload = fetch_json(url)
            chart = payload.get("chart", {})
            if chart.get("error"):
                description = chart["error"].get(
                    "description", "Chyba Yahoo Finance"
                )
                return None, f"Yahoo: {description}"
            results = chart.get("result") or []
            if not results:
                return None, "Yahoo nevrátilo data"
            result = results[0]
            timestamps = result.get("timestamp") or []
            groups = (
                result.get("indicators", {}).get("quote") or []
            )
            if not timestamps or not groups:
                return None, "Chybí historie cen"
            quote = groups[0]
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            observations = []
            for index, timestamp in enumerate(timestamps):
                close = closes[index] if index < len(closes) else None
                volume = volumes[index] if index < len(volumes) else 0
                if close is None:
                    continue
                try:
                    close = float(close)
                    volume = max(0, int(volume or 0))
                except (ValueError, TypeError):
                    continue
                if close <= 0:
                    continue
                observations.append({
                    "timestamp": int(timestamp),
                    "close": close,
                    "volume": volume,
                })
            if not observations:
                return None, "Žádné použitelné ceny"
            latest = observations[-1]
            previous = observations[-2] if len(observations) > 1 else None
            change = None
            if previous and previous["close"]:
                change = (
                    (latest["close"] - previous["close"])
                    / previous["close"] * 100
                )
            vols = [
                item["volume"]
                for item in observations[-20:]
                if item["volume"] > 0
            ]
            avg_volume = sum(vols) / len(vols) if vols else None
            events = result.get("events") or {}
            dividends = events.get("dividends") or {}
            cutoff = latest["timestamp"] - 365 * 86400
            annual_dividend = 0.0
            dividend_count = 0
            for event in dividends.values():
                try:
                    if int(event.get("date", 0)) >= cutoff:
                        amount = float(event.get("amount", 0))
                        if amount > 0:
                            annual_dividend += amount
                            dividend_count += 1
                except (ValueError, TypeError, AttributeError):
                    pass
            price = latest["close"]
            dividend_yield = (
                annual_dividend / price * 100 if price > 0 else None
            )
            return {
                "symbol": symbol,
                "name": instrument.get("name", symbol),
                "exchange": instrument.get("exchange", "Unknown"),
                "asset_type": "stock",
                "price": round(price, 6),
                "previous_close": round(previous["close"], 6) if previous else None,
                "change_percent": round(change, 4) if change is not None else None,
                "volume": latest["volume"],
                "average_volume": round(avg_volume, 2) if avg_volume is not None else None,
                "turnover_estimate": round(price * latest["volume"], 2),
                "currency": (result.get("meta") or {}).get("currency", "USD"),
                "market_cap": (result.get("meta") or {}).get("marketCap"),
                "dividend_per_share_year_estimate": round(annual_dividend, 6),
                "dividend_yield_percent_estimate": round(dividend_yield, 4) if dividend_yield is not None else None,
                "dividend_observations": dividend_count,
                "quote_timestamp": latest["timestamp"],
                "quote_date_utc": datetime.fromtimestamp(
                    latest["timestamp"], timezone.utc
                ).strftime("%Y-%m-%d"),
                "data_source": "Yahoo Finance chart endpoint",
            }, None
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code == 404:
                return None, last_error
            if error.code == 429:
                # Sdílená pauza i pro ostatní pracovní vlákna.
                wait_seconds = min(60 * (2 ** attempt), 600)
                extend_cooldown(wait_seconds)
                last_error = f"HTTP 429; čekání {wait_seconds}s"
            elif error.code in (408, 425, 500, 502, 503, 504):
                wait_seconds = min(5 * (2 ** attempt), 60)
                time.sleep(wait_seconds + random.uniform(0.2, 2.0))
            else:
                return None, last_error
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ) as error:
            last_error = f"{type(error).__name__}: {error}"
            time.sleep(min(3 * (2 ** attempt), 30))
        if attempt < RETRIES:
            wait_for_cooldown()
    return None, last_error
def rank(rows, key, reverse=True):
    valid = [row for row in rows if row.get(key) is not None]
    return sorted(valid, key=lambda row: row[key], reverse=reverse)[:TOP_COUNT]
def error_counts(errors):
    counts = {}
    for item in errors:
        key = item["error"].split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return counts
def build_market(total, rows, errors, started):
    gainers = [row for row in rows if row.get("change_percent") is not None]
    unusual = []
    for row in rows:
        avg = row.get("average_volume")
        volume = row.get("volume")
        if avg and avg > 0 and volume is not None:
            ratio = volume / avg
            if ratio >= 2:
                item = dict(row)
                item["volume_ratio"] = round(ratio, 2)
                unusual.append(item)
    unusual.sort(key=lambda item: item["volume_ratio"], reverse=True)
    return {
        "project": "HVLK AI",
        "generated_at": now_utc(),
        "market": "US",
        "asset_scope": "US stocks; symbol classification is not guaranteed perfect",
        "data_provider": "Yahoo Finance chart endpoint",
        "universe_symbols": total,
        "scanned_symbols": len(rows) + len(errors),
        "successful_quotes": len(rows),
        "failed_quotes": len(errors),
        "coverage_percent": round(len(rows) / total * 100, 2) if total else 0,
        "scan_started_at": started,
        "scan_finished_at": now_utc(),
        "top_gainers": rank(gainers, "change_percent", True),
        "top_losers": rank(gainers, "change_percent", False),
        "highest_volume": rank(rows, "volume", True),
        "highest_turnover": rank(rows, "turnover_estimate", True),
        "unusual_volume": unusual[:TOP_COUNT],
        "errors_by_type": error_counts(errors),
        "sample_errors": errors[:50],
        "data_notes": [
            "Denní závěrečné ceny, nikoli živé intradenní kotace.",
            "Změna porovnává poslední dvě dostupné denní závěrečné ceny.",
            "Obrat je odhad: poslední cena × poslední denní objem.",
            "Průměr objemu vychází z posledních až 20 dostupných dnů.",
            "Dividendové údaje jsou odhady z historických událostí poskytovatele.",
            "HTTP 404 znamená, že zdroj pro daný požadavek data nenašel.",
            "HTTP 429 znamená omezení požadavků poskytovatelem.",
        ],
    }
def save_all(total, rows, errors, started):
    write_json(RESULTS_FILE, {
        "project": "HVLK AI",
        "generated_at": now_utc(),
        "market": "US",
        "data_provider": "Yahoo Finance chart endpoint",
        "total_symbols": total,
        "successful_quotes": len(rows),
        "failed_quotes": len(errors),
        "coverage_percent": round(len(rows) / total * 100, 2) if total else 0,
        "results": rows,
        "errors": errors[:500],
    })
    write_json(MARKET_FILE, build_market(total, rows, errors, started))
def main():
    started = now_utc()
    instruments = load_universe()
    total = len(instruments)
    print(f"HVLK AI: {total} symbolů; souběžnost {WORKERS}.")
    rows_by_symbol = {}
    errors = []
    completed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_quote, item): item
            for item in instruments
        }
        for future in as_completed(futures):
            item = futures[future]
            symbol = item["symbol"]
            completed += 1
            try:
                row, error = future.result()
            except Exception as exception:
                row = None
                error = f"{type(exception).__name__}: {exception}"
            if row:
                rows_by_symbol[symbol] = row
            else:
                errors.append({
                    "symbol": symbol,
                    "name": item.get("name", symbol),
                    "error": error or "Neznámá chyba",
                })
            if completed % 25 == 0 or completed == total:
                print(
                    f"{completed}/{total} | "
                    f"úspěšné {len(rows_by_symbol)} | "
                    f"chyby {len(errors)}"
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
    save_all(total, current_rows, errors, started)
    summary = build_market(total, current_rows, errors, started)
    print("\n--- HVLK AI ---")
    print(f"Úspěšné ceny: {len(current_rows)}")
    print(f"Chyby: {len(errors)}")
    print(f"Pokrytí: {summary['coverage_percent']} %")
    print(f"Chyby podle typu: {summary['errors_by_type']}")
if __name__ == "__main__":
    main()
