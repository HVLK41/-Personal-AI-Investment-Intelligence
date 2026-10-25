import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "universe.json"
SOURCES = {
    "nasdaq": "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt",
    "other": "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt",
}
USER_AGENT = "HVLK-AI/1.0 personal research"
# Zjevné typy cenných papírů, které nechceme v základním
# seznamu běžných akcií. Jde o heuristický filtr, ne garanci.
EXCLUDE_PATTERNS = [
    r"\bWARRANTS?\b",
    r"\bRIGHTS?\b",
    r"\bUNITS?\b",
    r"\bPREFERRED(?:\s+STOCK|\s+SHARES?)?\b",
    r"\bDEBENTURES?\b",
    r"\bBONDS?\b",
    r"\bNOTES?\b",
    r"\bETFS?\b",
    r"\bEXCHANGE.TRADED.FUNDS?\b",
    r"\bPURCHASE.CONTRACTS?\b",
]
def fetch_text(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")
def is_excluded(name, symbol):
    upper_name = name.upper()
    if any(re.search(pattern, upper_name) for pattern in EXCLUDE_PATTERNS):
        return True
    # Samostatné warrant/rights/unit tickery bývají často
    # označené koncovkami. Nevyřazujeme běžné tečkové třídy akcií.
    upper_symbol = symbol.upper()
    if re.search(r"[-.]W(S|S[A-Z]?)?$", upper_symbol):
        return True
    if re.search(r"[-.]R(T|TS)?$", upper_symbol):
        return True
    return False
def parse_symbols(text, source):
    lines = text.splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"Zdroj {source} vrátil prázdný seznam.")
    headers = lines[0].split("|")
    accepted = []
    rejected = 0
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
        if not symbol:
            continue
        if row.get("Test Issue", "N").strip().upper() == "Y":
            rejected += 1
            continue
        if row.get("ETF", "N").strip().upper() == "Y":
            rejected += 1
            continue
        name = (
            row.get("Security Name")
            or row.get("Company Name")
            or symbol
        ).strip()
        if is_excluded(name, symbol):
            rejected += 1
            continue
        exchange = (
            row.get("Exchange", "").strip()
            or source
        )
        accepted.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "source": source,
            "asset_type": "stock",
        })
    return accepted, rejected
def main():
    instruments = {}
    source_counts = {}
    excluded_counts = {}
    errors = []
    for source, url in SOURCES.items():
        try:
            text = fetch_text(url)
            accepted, rejected = parse_symbols(text, source)
            source_counts[source] = len(accepted)
            excluded_counts[source] = rejected
            for item in accepted:
                key = item["symbol"].upper()
                if key not in instruments:
                    instruments[key] = item
        except Exception as error:
            errors.append(f"{source}: {type(error).__name__}: {error}")
    if not instruments:
        raise RuntimeError(
            "Nepodařilo se načíst žádné symboly. "
            + " | ".join(errors)
        )
    results = sorted(
        instruments.values(),
        key=lambda item: item["symbol"],
    )
    output = {
        "project": "HVLK AI",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "market": "US",
        "provider": "Nasdaq Trader symbol directories",
        "total_symbols": len(results),
        "source_counts": source_counts,
        "excluded_counts": excluded_counts,
        "partial_errors": errors,
        "results": results,
        "disclaimer": (
            "Seznam je heuristicky filtrován na běžné akcie. "
            "Klasifikace nemusí být dokonalá a seznam nemusí "
            "obsahovat všechny americké trhy. Dostupnost ceny "
            "u jednotlivých symbolů závisí na datovém poskytovateli."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT)
    print(f"Akcie v seznamu: {len(results)}")
    print(f"Vyřazeno podle filtrů: {sum(excluded_counts.values())}")
    print(f"Výstup: {OUTPUT}")
    for error in errors:
        print(f"VAROVÁNÍ: {error}")
if __name__ == "__main__":
    main()
