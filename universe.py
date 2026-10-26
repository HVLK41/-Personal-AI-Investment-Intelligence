

#!/usr/bin/env python3
"""HVLK AI — US equity universe builder.

Sources:
- Nasdaq Trader: nasdaqlisted.txt
- Nasdaq Trader: otherlisted.txt
- SEC: company_tickers_exchange.json

The output is data/universe.json.

Important:
- SEC is supplementary and may return HTTP 403.
- SEC ticker data does not reliably classify every security type.
- Filtering is conservative and rule-based, not a legal security master.
- Original exchange symbols are preserved; provider symbols are separate.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "universe.json"

SOURCES = {
    "nasdaq": (
        "https://www.nasdaqtrader.com/dynamic/symdir/"
        "nasdaqlisted.txt"
    ),
    "otherlisted": (
        "https://www.nasdaqtrader.com/dynamic/symdir/"
        "otherlisted.txt"
    ),
    "sec": "https://www.sec.gov/files/company_tickers_exchange.json",
}

USER_AGENT = (
    "HVLK-AI/1.1 "
    "(personal research; "
    "https://github.com/HVLK41/-Personal-AI-Investment-Intelligence)"
)

REQUEST_TIMEOUT = 30
MAX_SOURCE_BYTES = 25_000_000

# Reject clear non-common-equity descriptions.
# These are deliberately patterns rather than a blanket ban on
# every company containing words such as "capital" or "group".
EXCLUDE_NAME_PATTERNS = [
    r"\bEXCHANGE[\s-]+TRADED[\s-]+FUNDS?\b",
    r"\bETFS?\b",
    r"\bWARRANTS?\b",
    r"\bRIGHTS?\b",
    r"\bSUBSCRIPTION RIGHTS?\b",
    r"\bUNITS?\b",
    r"\bPREFERRED(?:\s+(?:STOCK|SHARES?))?\b",
    r"\bPREFERENCE SHARES?\b",
    r"\bDEBENTURES?\b",
    r"\bBONDS?\b",
    r"\bNOTES?\b",
    r"\bDEPOSITARY SHARES?\b",
    r"\bDEPOSIT(?:ARY)? UNITS?\b",
    r"\bPURCHASE CONTRACTS?\b",
    r"\bWHEN[\s-]+ISSUED\b",
    r"\bWHEN[\s-]+DISTRIBUTED\b",
    r"\bCONVERTIBLE NOTES?\b",
    r"\bCLOSED[\s-]+END FUNDS?\b",
    r"\bMONEY MARKET FUNDS?\b",
    r"\bMUTUAL FUNDS?\b",
    r"\bINCOME FUNDS?\b",
    r"\bINDEX FUNDS?\b",
    r"\bEXCHANGE TRADED PRODUCTS?\b",
    r"\bSTRUCTURED PRODUCTS?\b",
    r"\bETNS?\b",
    r"\bETCS?\b",
]

EXCLUDE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in EXCLUDE_NAME_PATTERNS
]

# Common suffixes for warrants, rights and units.
# These are secondary checks, not a replacement for security type data.
EXCLUDE_SYMBOL_SUFFIXES = [
    re.compile(r"[-.]W$"),
    re.compile(r"[-.]WS$"),
    re.compile(r"[-.]WT$"),
    re.compile(r"[-.]WTS$"),
    re.compile(r"[-.]R$"),
    re.compile(r"[-.]RT$"),
    re.compile(r"[-.]RTS$"),
    re.compile(r"[-.]U$"),
]

# Obvious fund/trust structures often missed by a generic ETF flag.
# Keep this conservative: "Trust" alone is not an exclusion because
# some operating companies and ADR structures use that word.
EXCLUDE_FUND_PATTERNS = [
    re.compile(
        r"\b(CLOSED[\s-]+END|MUTUAL|MONEY MARKET|INDEX|"
        r"INCOME|FIXED INCOME|BOND|CREDIT|MUNICIPAL|"
        r"REAL ESTATE INVESTMENT|INVESTMENT)\s+FUND\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(INVESTMENT TRUST|UNIT INVESTMENT TRUST|"
        r"BUSINESS DEVELOPMENT COMPANY)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(ETF|ETN|ETC|EXCHANGE TRADED FUND|"
        r"EXCHANGE-TRADED FUND)\b",
        re.IGNORECASE,
    ),
]

# Exclude non-trading test issues and clearly invalid symbols.
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=_-]{1,32}$")


def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=REQUEST_TIMEOUT,
    ) as response:
        raw = response.read(MAX_SOURCE_BYTES + 1)

    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("Source exceeds maximum permitted size.")

    return raw.decode("utf-8-sig")


def valid_symbol(symbol: str) -> bool:
    symbol = symbol.strip()
    return bool(SYMBOL_RE.fullmatch(symbol))


def provider_symbol(symbol: str) -> str:
    """Map a symbol to a Yahoo-style ticker without changing its identity.

    The original exchange symbol remains in the 'symbol' field.
    This mapping is only a provider hint; it is not used for deduplication.
    """
    return symbol.strip().upper().replace(".", "-")


def is_excluded(
    name: str,
    symbol: str,
    *,
    is_etf: bool = False,
) -> tuple[bool, str | None]:
    """Return (excluded, reason)."""

    clean_name = " ".join((name or "").split())
    clean_symbol = (symbol or "").strip().upper()

    if is_etf:
        return True, "source_marks_etf"

    for pattern in EXCLUDE_PATTERNS:
        if pattern.search(clean_name):
            return True, "non_common_equity_name"

    for pattern in EXCLUDE_FUND_PATTERNS:
        if pattern.search(clean_name):
            return True, "fund_or_investment_vehicle"

    for pattern in EXCLUDE_SYMBOL_SUFFIXES:
        if pattern.search(clean_symbol):
            return True, "non_common_equity_symbol_suffix"

    return False, None


def parse_nasdaq_directory(
    text: str,
    source: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    lines = text.splitlines()

    # Nasdaq Trader files may include a metadata line before the header.
    header_index = next(
        (
            index
            for index, line in enumerate(lines[:10])
            if "Symbol" in line and "|" in line
        ),
        None,
    )

    if header_index is None:
        raise ValueError("Could not find the Nasdaq Trader header.")

    headers = lines[header_index].split("|")
    records: list[dict[str, Any]] = []
    stats = {
        "rows_seen": 0,
        "included": 0,
        "excluded_test_issue": 0,
        "excluded_etf": 0,
        "excluded_other": 0,
        "invalid_symbol": 0,
    }

    for line in lines[header_index + 1:]:
        if not line or line.startswith("File Creation Time"):
            continue

        values = line.split("|")
        if len(values) != len(headers):
            continue

        row = dict(zip(headers, values))
        stats["rows_seen"] += 1

        symbol = (
            row.get("Symbol")
            or row.get("ACT Symbol")
            or ""
        ).strip()

        if not valid_symbol(symbol):
            stats["invalid_symbol"] += 1
            continue

        if row.get("Test Issue", "N").strip().upper() == "Y":
            stats["excluded_test_issue"] += 1
            continue

        is_etf = row.get("ETF", "N").strip().upper() == "Y"
        name = (
            row.get("Security Name")
            or row.get("Company Name")
            or symbol
        ).strip()

        excluded, reason = is_excluded(
            name,
            symbol,
            is_etf=is_etf,
        )

        if excluded:
            if reason == "source_marks_etf":
                stats["excluded_etf"] += 1
            else:
                stats["excluded_other"] += 1
            continue

        exchange = (
            row.get("Exchange", "").strip()
            or source
        )

        records.append({
            "symbol": symbol,
            "provider_symbol": provider_symbol(symbol),
            "name": name,
            "exchange": exchange,
            "source": source,
            "asset_type": "equity_candidate",
            "classification": "rule_filtered_candidate",
        })
        stats["included"] += 1

    if stats["rows_seen"] == 0:
        raise ValueError("No data rows were found in Nasdaq Trader file.")

    return records, stats


def parse_sec_directory(
    text: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    payload = json.loads(text)

    fields = payload.get("fields", [])
    rows = payload.get("data", [])

    if not isinstance(fields, list) or not isinstance(rows, list):
        raise ValueError("Unexpected SEC JSON schema.")

    if not fields:
        raise ValueError("SEC JSON contains no field definitions.")

    records: list[dict[str, Any]] = []
    stats = {
        "rows_seen": 0,
        "included": 0,
        "excluded_other": 0,
        "invalid_symbol": 0,
    }

    for values in rows:
        if not isinstance(values, list) or len(values) != len(fields):
            continue

        row = dict(zip(fields, values))
        stats["rows_seen"] += 1

        symbol = str(row.get("ticker") or "").strip()
        name = str(row.get("name") or symbol).strip()
        exchange = str(row.get("exchange") or "SEC-UNKNOWN").strip()

        if not valid_symbol(symbol):
            stats["invalid_symbol"] += 1
            continue

        excluded, _reason = is_excluded(name, symbol)

        if excluded:
            stats["excluded_other"] += 1
            continue

        cik = row.get("cik")
        try:
            cik = int(cik) if cik is not None else None
        except (TypeError, ValueError):
            cik = None

        records.append({
            "symbol": symbol,
            "provider_symbol": provider_symbol(symbol),
            "name": name,
            "exchange": exchange,
            "source": "sec",
            "asset_type": "equity_candidate",
            "classification": "rule_filtered_candidate",
            "cik": cik,
        })
        stats["included"] += 1

    if stats["rows_seen"] == 0:
        raise ValueError("SEC response contains no data rows.")

    return records, stats


def merge_instruments(
    instruments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate by exact normalized exchange symbol.

    Do not automatically equate ABC.A with ABC-A: those can represent
    different symbols. Provider-specific ticker normalization is separate.
    """
    merged: dict[str, dict[str, Any]] = {}
    duplicates = 0

    for item in instruments:
        symbol = str(item.get("symbol") or "").strip()
        if not valid_symbol(symbol):
            continue

        key = symbol.upper()

        if key not in merged:
            merged[key] = {
                **item,
                "symbol": symbol,
                "sources": [item.get("source", "unknown")],
                "exchanges": [item.get("exchange", "unknown")],
            }
            continue

        duplicates += 1
        current = merged[key]

        source = item.get("source", "unknown")
        exchange = item.get("exchange", "unknown")

        if source not in current["sources"]:
            current["sources"].append(source)

        if exchange not in current["exchanges"]:
            current["exchanges"].append(exchange)

        # Keep the more informative name if the existing one is blank.
        if (
            not current.get("name")
            or current["name"] == current["symbol"]
        ) and item.get("name"):
            current["name"] = item["name"]

        if not current.get("cik") and item.get("cik"):
            current["cik"] = item["cik"]

        # Prefer a source's provider mapping only if it is absent.
        if not current.get("provider_symbol") and item.get("provider_symbol"):
            current["provider_symbol"] = item["provider_symbol"]

    results = sorted(
        merged.values(),
        key=lambda item: item["symbol"].upper(),
    )

    for item in results:
        item["sources"] = sorted(set(item["sources"]))
        item["exchanges"] = sorted(set(item["exchanges"]))

    return results, duplicates


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    collected: list[dict[str, Any]] = []
    source_status: dict[str, dict[str, Any]] = {}
    source_stats: dict[str, dict[str, int]] = {}
    errors: list[str] = []

    for source, url in SOURCES.items():
        try:
            text = fetch_text(url)

            if source == "sec":
                records, stats = parse_sec_directory(text)
            else:
                records, stats = parse_nasdaq_directory(text, source)

            source_stats[source] = stats
            source_status[source] = {
                "status": "success",
                "records_included": len(records),
                "url": url,
            }
            collected.extend(records)

            print(
                f"{source}: included={len(records)}, "
                f"rows_seen={stats['rows_seen']}"
            )

        except Exception as error:
            message = f"{source}: {type(error).__name__}: {error}"
            errors.append(message)
            source_status[source] = {
                "status": "failed",
                "records_included": 0,
                "url": url,
                "error": message,
            }
            print(f"WARNING: {message}")

    # Nasdaq Trader's two files are the core listing inputs.
    # Do not silently publish a supposedly complete universe if either
    # core input fails. SEC may be unavailable, but that is explicitly
    # reflected in source_status and partial_errors.
    core_sources = ("nasdaq", "otherlisted")
    failed_core = [
        source for source in core_sources
        if source_status.get(source, {}).get("status") != "success"
    ]

    if failed_core:
        raise RuntimeError(
            "Core listing source failure; refusing to write a new universe: "
            + ", ".join(failed_core)
            + ". Previous data/universe.json remains unchanged."
        )

    if not collected:
        raise RuntimeError(
            "No usable equity candidates were collected. "
            + " | ".join(errors)
        )

    results, duplicate_count = merge_instruments(collected)

    if not results:
        raise RuntimeError("Filtering produced an empty universe.")

    sec_ok = source_status.get("sec", {}).get("status") == "success"
    universe_status = "complete" if sec_ok else "partial"

    payload = {
        "project": "HVLK AI",
        "schema_version": 2,
        "generated_at": now_utc(),
        "market": "US",
        "universe_status": universe_status,
        "provider": (
            "Nasdaq Trader + SEC"
            if sec_ok
            else "Nasdaq Trader (SEC unavailable)"
        ),
        "total_symbols": len(results),
        "raw_records": len(collected),
        "duplicates_merged": duplicate_count,
        "source_counts": {
            source: source_status.get(source, {}).get(
                "records_included", 0
            )
            for source in SOURCES
        },
        "source_status": source_status,
        "source_stats": source_stats,
        "partial_errors": errors,
        "results": results,
        "disclaimer": (
            "This is a rule-filtered US equity candidate universe, "
            "not a guaranteed complete security master. Nasdaq Trader "
            "and SEC sources can be incomplete or unavailable. The SEC "
            "ticker file does not reliably identify all security types. "
            "Name and symbol filters can miss unusual instruments or "
            "exclude exceptional cases. Review classification and "
            "source_status before treating the universe as complete. "
            "Original exchange symbols are preserved; provider_symbol "
            "is only a provider-format hint."
        ),
    }

    write_json(OUTPUT, payload)

    print("\n--- HVLK AI UNIVERSE ---")
    print(f"Status: {universe_status}")
    print(f"Source records: {len(collected)}")
    print(f"Merged duplicates: {duplicate_count}")
    print(f"Unique symbols: {len(results)}")
    print(f"SEC available: {sec_ok}")
    print(f"Output: {OUTPUT}")

    for error in errors:
        print(f"PARTIAL SOURCE ERROR: {error}")


if __name__ == "__main__":
    main()
