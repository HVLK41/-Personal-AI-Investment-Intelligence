import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = PROJECT_ROOT / "data" / "universe.json"
SOURCES = {
    "nasdaq": (
        "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
    ),
    "other": (
        "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"
    ),
}
def fetch_text(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HVLK-AI/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")
def parse_symbols(text, source):
    lines = text.splitlines()
    if len(lines) < 2:
        raise RuntimeError(f"Zdroj {source} vrátil prázdný seznam.")
    headers = lines[0].split("|")
    instruments = []
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
        if row.get("Test Issue", "N") == "Y":
            continue
        if row.get("ETF", "N") == "Y":
            continue
        if row.get("Exchange", "").strip() == "":
            # Nasdaq seznam používá vlastní pole Exchange.
            # Pokud burza není uvedena, zachováme původ zdroje.
            exchange = source
        else:
            exchange = row["Exchange"].strip()
        name = (
            row.get("Security Name")
            or row.get("Company Name")
            or symbol
        ).strip()
        instruments.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "source": source,
            "asset_type": "stock",
        })
    return instruments
def main():
    all_instruments = {}
    source_counts = {}
    errors = []
    for source, url in SOURCES.items():
        try:
            text = fetch_text(url)
            instruments = parse_symbols(text, source)
            source_counts[source] = len(instruments)
            for instrument in instruments:
                key = instrument["symbol"].upper()
                if key not in all_instruments:
                    all_instruments[key] = instrument
        except Exception as error:
            errors.append(f"{source}: {error}")
    # Nepřepisujeme fungující seznam prázdným výsledkem,
    # pokud selžou oba zdroje.
    if not all_instruments:
        raise RuntimeError(
            "Nepodařilo se načíst žádné symboly. "
            + " | ".join(errors)
        )
    results = sorted(
        all_instruments.values(),
        key=lambda item: item["symbol"],
    )
    output = {
        "project": PROJECT_ROOT.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "Nasdaq Trader symbol directories",
        "total_symbols": len(results),
        "source_counts": source_counts,
        "partial_errors": errors,
        "results": results,
        "disclaimer": (
            "Seznam obsahuje instrumenty nalezené ve zdrojových "
            "adresářích. Nezaručuje úplné pokrytí všech amerických "
            "tržišť ani dostupnost aktuálních cen pro každý symbol."
        ),
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Načteno symbolů: {len(results)}")
    print(f"Výstup: {OUTPUT_FILE}")
    if errors:
        print("Varování:")
        for error in errors:
            print(f"- {error}")
if __name__ == "__main__":
    main()
