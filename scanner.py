import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ.get("FMP_API_KEY", "").strip()
BASE_URL = "https://financialmodelingprep.com/stable/"

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


def fetch(endpoint, params):
    """Načte JSON z FMP bez vypisování API klíče do logu."""
    query = dict(params)
    query["apikey"] = API_KEY

    url = BASE_URL + endpoint + "?" + urllib.parse.urlencode(query)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "HVLK-AI/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8")

    except urllib.error.HTTPError as error:
        try:
            details = error.read().decode(
                "utf-8",
                errors="replace",
            )
        except Exception:
            details = ""

        details = " ".join(details.split())[:400]

        message = f"FMP HTTP {error.code}"

        if details:
            message += f": {details}"

        raise RuntimeError(message) from None

    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Chyba připojení k FMP: {error.reason}"
        ) from None

    try:
        return json.loads(body)

    except json.JSONDecodeError:
        raise RuntimeError(
            "FMP vrátil neplatnou JSON odpověď."
        ) from None


def first_record(data):
    """Získá první použitelný záznam odpovědi."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict):
            return data[0]

        return None

    if isinstance(data, dict):
        if "Error Message" in data or "error" in data:
            return None

        return data

    return None


def parse_number(value):
    """Bezpečně převede hodnotu na číslo."""
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def scan_symbol(symbol):
    result = {
        "symbol": symbol,
        "price": None,
        "change_percent": None,
        "dividend_per_share_year_estimate": None,
        "dividend_yield_percent_estimate": None,
        "dividend_observations": 0,
        "status": "error",
        "notes": [],
    }

    # 1. Získání ceny akcie nebo ETF.
    try:
        quote_data = fetch(
            "quote",
            {"symbol": symbol},
        )

        quote = first_record(quote_data)

        if not quote:
            result["status"] = "no_quote"
            result["notes"].append(
                "FMP nevrátil použitelný záznam ceny. "
                "Ověř dostupnost endpointu a tickeru ve svém tarifu."
            )

            if isinstance(quote_data, dict):
                detail = (
                    quote_data.get("Error Message")
                    or quote_data.get("error")
                )

                if detail:
                    result["notes"].append(str(detail)[:300])

            return result

        price = parse_number(quote.get("price"))

        if price is None or price <= 0:
            result["status"] = "no_quote"
            result["notes"].append(
                "Odpověď FMP neobsahuje platnou cenu."
            )

            return result

        result["price"] = price

        result["change_percent"] = parse_number(
            quote.get("changePercentage")
        )

        result["company_name"] = quote.get("name")
        result["currency"] = quote.get("currency")
        result["exchange"] = quote.get("exchange")
        result["quote_timestamp"] = quote.get("timestamp")

        result["status"] = "ok"

    except Exception as error:
        result["notes"].append(
            f"Cena: {str(error)[:400]}"
        )

        return result

    # 2. Získání dividendových údajů.
    try:
        dividends_data = fetch(
            "dividends",
            {"symbol": symbol},
        )

        if not isinstance(dividends_data, list):
            dividends_data = []

        today = datetime.now(timezone.utc).date()
        recent_dividends = []

        for item in dividends_data:
            if not isinstance(item, dict):
                continue

            date_text = item.get("date")
            amount = parse_number(item.get("dividend"))

            if not date_text or amount is None:
                continue

            try:
                dividend_date = datetime.strptime(
                    str(date_text)[:10],
                    "%Y-%m-%d",
                ).date()

            except ValueError:
                continue

            days_old = (today - dividend_date).days

            if 0 <= days_old <= 365:
                recent_dividends.append(amount)

        annual_dividend = sum(recent_dividends)

        result["dividend_per_share_year_estimate"] = round(
            annual_dividend,
            4,
        )

        result["dividend_observations"] = len(
            recent_dividends
        )

        if result["price"] and result["price"] > 0:
            result["dividend_yield_percent_estimate"] = round(
                annual_dividend / result["price"] * 100,
                3,
            )

    except Exception as error:
        result["notes"].append(
            "Dividendová data nejsou dostupná: "
            + str(error)[:400]
        )

    return result


def main():
    if not API_KEY:
        raise RuntimeError(
            "Chybí FMP_API_KEY. V GitHubu otevři "
            "Settings → Secrets and variables → Actions "
            "a ověř repository secret FMP_API_KEY."
        )

    results = []

    for symbol in SYMBOLS:
        print(f"Analyzuji: {symbol}")

        item = scan_symbol(symbol)
        results.append(item)

        print(
            f"  Stav: {item['status']}; "
            f"cena: {item['price']}"
        )

        for note in item["notes"]:
            print(f"  Informace: {note}")

    # Seřazení podle odhadovaného dividendového výnosu.
    results.sort(
        key=lambda item: (
            item["dividend_yield_percent_estimate"] is not None,
            item["dividend_yield_percent_estimate"] or 0,
        ),
        reverse=True,
    )

    successful = sum(
        1
        for item in results
        if item["price"] is not None
    )

    output = {
        "project": "HVLK AI",
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "data_provider": "Financial Modeling Prep",
        "scan_type": "initial_watchlist",
        "successful_quotes": successful,
        "total_symbols": len(results),
        "results": results,
        "disclaimer": (
            "Orientační automatický sken, nikoli investiční "
            "doporučení. Dividendový výnos je odhad součtu "
            "dividend zaznamenaných za posledních 365 dní "
            "děleného poslední dostupnou cenou. Nezaručuje "
            "budoucí výplatu. Chybějící data neznamenají "
            "nulovou dividendu. Data mohou být zpožděná "
            "nebo omezená tarifem poskytovatele."
        ),
    }

    output_path = Path("data/results.json")
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        f"Dokončeno: {successful}/{len(results)} "
        "symbolů s cenou."
    )

    if successful == 0:
        print(
            "VAROVÁNÍ: Nebyla načtena žádná cena. "
            "Podrobnosti jsou v data/results.json."
        )

        raise RuntimeError(
            "Scanner nezískal žádné ceny. "
            "Otevři data/results.json a zkontroluj "
            "pole notes u jednotlivých tickerů."
        )


if __name__ == "__main__":
    main()
