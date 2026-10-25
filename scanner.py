import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
PROJECT = "HVLK AI"
SYMBOLS = [
    "AAPL",
    "MSFT",
    "O",
    "MAIN",
    "KO",
    "JNJ",
    "AGNC",
    "VICI",
    "SCHD",
    "JEPI",
]
BASE_URL = "https://query1.finance.yahoo.com"
def fetch_json(url):
    """Načte JSON přes HTTPS a vrátí srozumitelnou chybu."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"Yahoo Finance HTTP {error.code}"
        ) from None
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Chyba připojení: {error.reason}"
        ) from None
    except json.JSONDecodeError:
        raise RuntimeError(
            "Zdroj vrátil neplatnou JSON odpověď."
        ) from None
def get_chart(symbol, period1, period2):
    """Načte historické ceny a případné dividendové události."""
    params = {
        "period1": str(period1),
        "period2": str(period2),
        "interval": "1d",
        "events": "div,splits",
    }
    url = (
        f"{BASE_URL}/v8/finance/chart/"
        f"{urllib.parse.quote(symbol)}?"
        f"{urllib.parse.urlencode(params)}"
    )
    data = fetch_json(url)
    chart = data.get("chart", {})
    error = chart.get("error")
    if error:
        raise RuntimeError(str(error)[:300])
    results = chart.get("result")
    if not results:
        raise RuntimeError("Zdroj nevrátil žádná tržní data.")
    return results[0]
def scan_symbol(symbol):
    result = {
        "symbol": symbol,
        "price": None,
        "currency": None,
        "company_name": None,
        "change_percent": None,
        "dividend_per_share_year_estimate": None,
        "dividend_yield_percent_estimate": None,
        "dividend_observations": 0,
        "status": "error",
        "notes": [],
    }
    now = int(time.time())
    year_ago = now - 366 * 24 * 60 * 60
    try:
        data = get_chart(symbol, year_ago, now + 60)
        timestamps = data.get("timestamp") or []
        indicators = data.get("indicators") or {}
        quotes = indicators.get("quote") or []
        if not timestamps or not quotes:
            raise RuntimeError("Chybí cenová historie.")
        closes = quotes[0].get("close") or []
        valid_prices = [
            (timestamp, float(price))
            for timestamp, price in zip(timestamps, closes)
            if price is not None
        ]
        if not valid_prices:
            raise RuntimeError("Nebyly nalezeny platné ceny.")
        latest_timestamp, latest_price = valid_prices[-1]
        result["price"] = round(latest_price, 6)
        if len(valid_prices) >= 2:
            previous_price = valid_prices[-2][1]
            if previous_price:
                result["change_percent"] = round(
                    (latest_price / previous_price - 1) * 100,
                    4,
                )
        meta = data.get("meta") or {}
        result["currency"] = meta.get("currency")
        result["company_name"] = (
            meta.get("longName")
            or meta.get("shortName")
            or symbol
        )
        result["exchange"] = meta.get("exchangeName")
        result["quote_timestamp"] = latest_timestamp
        # Zatím máme cenu; dividendy zpracujeme samostatně.
        result["status"] = "ok"
        events = data.get("events") or {}
        dividend_events = events.get("dividends") or {}
        cutoff = now - 365 * 24 * 60 * 60
        dividend_values = []
        for timestamp, event in dividend_events.items():
            try:
                event_timestamp = int(timestamp)
                amount = float(event.get("amount", 0))
                if cutoff <= event_timestamp <= now and amount > 0:
                    dividend_values.append(amount)
            except (TypeError, ValueError, AttributeError):
                continue
        annual_dividend = sum(dividend_values)
        result["dividend_per_share_year_estimate"] = round(
            annual_dividend,
            6,
        )
        result["dividend_observations"] = len(dividend_values)
        if latest_price > 0:
            result["dividend_yield_percent_estimate"] = round(
                annual_dividend / latest_price * 100,
                4,
            )
        # Yahoo chart nemusí vrátit oficiální název společnosti.
        # Chybějící název nemá vliv na cenu ani dividendy.
    except Exception as error:
        result["status"] = "error"
        result["notes"].append(str(error)[:300])
    return result
def main():
    results = []
    for index, symbol in enumerate(SYMBOLS):
        print(f"Analyzuji: {symbol}")
        item = scan_symbol(symbol)
        results.append(item)
        print(
            f"  Stav: {item['status']}; "
            f"cena: {item['price']}; "
            f"dividendový výnos: "
            f"{item['dividend_yield_percent_estimate']}"
        )
        for note in item["notes"]:
            print(f"  Chyba: {note}")
        if index < len(SYMBOLS) - 1:
            time.sleep(1)
    results.sort(
        key=lambda item: (
            item["dividend_yield_percent_estimate"] is not None,
            item["dividend_yield_percent_estimate"] or 0,
        ),
        reverse=True,
    )
    successful = sum(
        1 for item in results if item["price"] is not None
    )
    output = {
        "project": PROJECT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_provider": "Yahoo Finance chart endpoint",
        "scan_type": "initial_watchlist",
        "successful_quotes": successful,
        "total_symbols": len(results),
        "results": results,
        "disclaimer": (
            "Orientační automatický sken, nikoli investiční "
            "doporučení. Ceny mohou být zpožděné. Dividendový "
            "výnos je odhad z dividendových událostí za posledních "
            "365 dní a poslední dostupné ceny. Není zárukou "
            "budoucích výplat. Neúplná dividendová historie může "
            "vést k podhodnocení výnosu. Dostupnost zdroje není "
            "garantována a může podléhat omezením."
        ),
    }
    output_path = Path("data/results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Dokončeno: {successful}/{len(results)} "
        "tickerů s cenou."
    )
    if successful == 0:
        raise RuntimeError(
            "Nepodařilo se načíst žádné ceny. "
            "Zkontroluj chyby ve výpisu a v data/results.json."
        )
if __name__ == "__main__":
    main()
