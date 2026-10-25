import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
API_KEY = os.environ.get("FMP_API_KEY", "").strip()
# První testovací skupina amerických akcií.
# Později rozšíříme na širší globální trh.
SYMBOLS = [
    "AAPL", "MSFT", "O", "MAIN",
    "KO", "JNJ", "AGNC", "VICI",
    "SCHD", "JEPI"
]
BASE_URL = "https://financialmodelingprep.com/stable/"
def fetch(endpoint, params):
    params["apikey"] = API_KEY
    url = BASE_URL + endpoint + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HVLK-AI/1.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
def get_first_record(data):
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None
def scan_symbol(symbol):
    result = {
        "symbol": symbol,
        "price": None,
        "change_percent": None,
        "dividend_per_share_year_estimate": None,
        "dividend_yield_percent_estimate": None,
        "status": "unknown",
        "notes": []
    }
    try:
        quote_data = fetch("quote", {"symbol": symbol})
         print(f"DEBUG {symbol}: {quote_data}")
        quote = get_first_record(quote_data)
        if not quote or not quote.get("price"):
            result["status"] = "no_quote"
            result["notes"].append(
                "Quote endpoint nevrátil použitelnou cenu."
            )
            return result
        price = float(quote["price"])
        result["price"] = price
        change = quote.get("changePercentage")
        if change is not None:
            result["change_percent"] = float(change)
        result["company_name"] = quote.get("name")
        result["currency"] = quote.get("currency")
        result["exchange"] = quote.get("exchange")
        result["quote_timestamp"] = quote.get("timestamp")
        try:
            dividends_data = fetch("dividends", {"symbol": symbol})
            dividends = (
                dividends_data
                if isinstance(dividends_data, list)
                else []
            )
            # Počítáme pouze platby za posledních přibližně 365 dní.
            today = datetime.now(timezone.utc).date()
            recent_dividends = []
            for item in dividends:
                date_text = item.get("date")
                amount = item.get("dividend")
                if not date_text or amount is None:
                    continue
                try:
                    dividend_date = datetime.strptime(
                        date_text[:10], "%Y-%m-%d"
                    ).date()
                    days_old = (today - dividend_date).days
                    if 0 <= days_old <= 365:
                        recent_dividends.append(float(amount))
                except (ValueError, TypeError):
                    continue
            annual_dividend = sum(recent_dividends)
            result["dividend_per_share_year_estimate"] = round(
                annual_dividend, 4
            )
            if price > 0:
                result["dividend_yield_percent_estimate"] = round(
                    annual_dividend / price * 100, 3
                )
            result["dividend_observations"] = len(recent_dividends)
        except Exception as error:
            result["notes"].append(
                "Dividendová data se nepodařilo načíst: "
                + str(error)[:200]
            )
        result["status"] = "ok"
    except Exception as error:
        result["status"] = "error"
        result["notes"].append(str(error)[:300])
    return result
def main():
    if not API_KEY:
        raise RuntimeError(
            "Chybí FMP_API_KEY. Zkontroluj Repository Secrets."
        )
    results = []
    for symbol in SYMBOLS:
        print("Analyzuji:", symbol)
        results.append(scan_symbol(symbol))
    # Řadíme pouze podle orientačního dividendového výnosu.
    # Není to doporučení k nákupu.
    results.sort(
        key=lambda item: (
            item["dividend_yield_percent_estimate"] is not None,
            item["dividend_yield_percent_estimate"] or 0
        ),
        reverse=True
    )
    output = {
        "project": "HVLK AI",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_provider": "Financial Modeling Prep",
        "scan_type": "initial_watchlist",
        "results": results,
        "disclaimer": (
            "Orientační automatický sken, nikoli investiční doporučení. "
            "Výnos je odhad z dividend zaznamenaných za posledních "
            "365 dní a aktuální ceny. Nezaručuje budoucí výplatu. "
            "Chybějící data neznamenají nulovou dividendu."
        )
    }
    output_path = Path("data/results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    successful = sum(1 for item in results if item["status"] == "ok")
    print(f"Dokončeno: {successful}/{len(results)} symbolů.")
    if successful == 0:
        raise RuntimeError(
            "Nepodařilo se získat žádné použitelné ceny. "
            "Zkontroluj API klíč, tarif a dostupnost endpointu."
        )
if __name__ == "__main__":
    main()
