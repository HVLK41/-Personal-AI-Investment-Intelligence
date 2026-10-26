
#!/usr/bin/env python3
"""HVLK AI — central US equity universe builder."""

from __future__ import annotations

import json
import re
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
    "sec": (
        "https://www.sec.gov/files/company_tickers_exchange.json"
    ),
}

USER_AGENT = (
    "HVLK-AI/1.0 personal research "
    "(https://github.com/HVLK41/-Personal-AI-Investment-Intelligence)"
)

# Heuristické vyloučení instrumentů, které nejsou běžné akcie.
# Není to dokonalá klasifikace; neznámé typy raději evidujeme.
EXCLUDE_NAME_PATTERNS = [
    r"\bWARRANTS?\b",
    r"\bRIGHTS?\b",
    r"\bUNITS?\b",
    r"\bPREFERRED(?:\s+STOCK|\s+SHARES?)?\b",
    r"\bDEBENTURES?\b",
    r"\bBONDS?\b",
    r"\bNOTES?\b",
    r"\bETFS?\b",
    r"\bEXCHANGE[\s-]+TRADED[\s-]+FUNDS?\b",
    r"\bPURCHASE[\s-]+CONTRACTS?\b",
]

# Pomocný klíč pro porovnání symbolů u poskytovatele,
# který běžně používá pomlčku místo tečky.
def symbol_key(symbol: str) -> str:
    return symbol.strip().upper().replace(".", "-")


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
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def valid_symbol(symbol: str) -> bool:
    return bool(
        symbol
        and len(symbol) <= 32
        and re.fullmatch(r"[A-Za-z0-9.^=_-]+", symbol)
    )


def excluded_instrument(name: str, symbol: str) -> bool:
    upper_name = name.upper()

    if any(
        re.search(pattern, upper_name)
        for pattern in EXCLUDE_NAME_PATTERNS
    ):
        return True

    upper_symbol = symbol.upper()

    # Běžné koncovky warrantů a práv.
    if re.search(r"[-.]W(S|S[A-Z]?)?$", upper_symbol):
        return True

    if re.search(r"[-.]R(T|TS)?$", upper_symbol):
        return True

    return False


def parse_nasdaq_directory(
    text: str,
    source: str,
) -> list[dict[str, Any]]:
    lines = text.splitlines()

    if len(lines) < 2:
        raise ValueError(f"Zdroj {source} vrátil prázdný seznam.")

    headers = lines[0].split("|")
    results = []

    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue

        values = line.split("|")
        if len(values) != len(headers):
            continue

        row = dict(zip(headers, values))

        symbol = (
            row.get("Symbol")
            or row.get("ACT Symbol")
            or ""
        ).strip()

        if not valid_symbol(symbol):
            continue

        if row.get("Test Issue", "N").strip().upper() == "Y":
            continue

        if row.get("ETF", "N").strip().upper() == "Y":
            continue

        name = (
            row.get("Security Name")
            or row.get("Company Name")
            or symbol
        ).strip()

        if excluded_instrument(name, symbol):
            continue

        exchange = (
            row.get("Exchange", "").strip()
            or source
        )

        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "source": source,
            "asset_type": "stock",
        })

    return results


def parse_sec_directory(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)

    fields = payload.get("fields", [])
    rows = payload.get("data", [])

    if not fields or not isinstance(rows, list):
        raise ValueError("SEC seznam má neočekávaný formát.")

    results = []

    for values in rows:
        if not isinstance(values, list) or len(values) != len(fields):
            continue

        row = dict(zip(fields, values))

        symbol = str(row.get("ticker") or "").strip()
        name = str(row.get("name") or symbol).strip()
        exchange = str(row.get("exchange") or "SEC-UNKNOWN").strip()

        if not valid_symbol(symbol):
            continue

        if excluded_instrument(name, symbol):
            continue

        results.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "source": "sec",
            "asset_type": "stock",
            "cik": row.get("cik"),
        })

    return results


def merge_instruments(
    instruments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}
    duplicates = 0

    for item in instruments:
        symbol = str(item.get("symbol") or "").strip()

        if not valid_symbol(symbol):
            continue

        key = symbol_key(symbol)

        if key not in merged:
            merged[key] = {
                **item,
                "symbol": symbol,
                "sources": [item.get("source", "unknown")],
                "exchanges": [item.get("exchange", "unknown")],
            }
            continue

        # Už existující instrument: zachováme metadata z dalších zdrojů.
        duplicates += 1
        current = merged[key]

        source = item.get("source", "unknown")
        exchange = item.get("exchange", "unknown")

        if source not in current["sources"]:
            current["sources"].append(source)

        if exchange not in current["exchanges"]:
            current["exchanges"].append(exchange)

        # Preferujeme název z prvního důvěryhodného zdroje,
        # ale doplníme chybějící údaje z dalších zdrojů.
        if not current.get("name") and item.get("name"):
            current["name"] = item["name"]

        if not current.get("cik") and item.get("cik"):
            current["cik"] = item["cik"]

    results = sorted(
        merged.values(),
        key=lambda item: item["symbol"].upper(),
    )

    for item in results:
        item["sources"] = sorted(set(item["sources"]))
        item["exchanges"] = sorted(set(item["exchanges"]))
        # Primární source/exchange zůstává zachován kvůli kompatibilitě.

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
    source_counts: dict[str, int] = {}
    errors: list[str] = []

    for source, url in SOURCES.items():
        try:
            text = fetch_text(url)

            if source == "sec":
                records = parse_sec_directory(text)
            else:
                records = parse_nasdaq_directory(text, source)

            source_counts[source] = len(records)
            collected.extend(records)

            print(f"{source}: načteno {len(records)} instrumentů")

        except Exception as error:
            message = f"{source}: {type(error).__name__}: {error}"
            errors.append(message)
            print(f"VAROVÁNÍ: {message}")

    if not collected:
        raise RuntimeError(
            "Žádný zdroj neposkytl použitelné instrumenty. "
            + " | ".join(errors)
        )

    results, duplicate_count = merge_instruments(collected)

    payload = {
        "project": "HVLK AI",
        "generated_at": now_utc(),
        "market": "US",
        "provider": "Nasdaq Trader + SEC",
        "total_symbols": len(results),
        "raw_records": len(collected),
        "duplicates_merged": duplicate_count,
        "source_counts": source_counts,
        "partial_errors": errors,
        "results": results,
        "disclaimer": (
            "Universe sjednocuje symboly z Nasdaq Trader a SEC. "
            "Seznam není zárukou úplného pokrytí všech amerických trhů. "
            "Klasifikace běžných akcií používá heuristické filtry. "
            "Dostupnost dat závisí na datovém poskytovateli."
        ),
    }

    write_json(OUTPUT, payload)

    print("\n--- HVLK AI UNIVERSE ---")
    print(f"Zdrojových záznamů: {len(collected)}")
    print(f"Sloučených duplicit: {duplicate_count}")
    print(f"Unikátních symbolů: {len(results)}")
    print(f"Výstup: {OUTPUT}")

    for error in errors:
        print(f"ČÁSTEČNÁ CHYBA: {error}")


if __name__ == "__main__":
    main()
