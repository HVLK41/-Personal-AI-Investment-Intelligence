import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UNIVERSE_FILE = DATA_DIR / "universe.json"
RESULTS_FILE = DATA_DIR / "results.json"
MARKET_FILE = DATA_DIR / "market.json"
BASE_URL = "https://query1.finance.yahoo.com"
# Omezujeme rychlost požadavků, abychom zbytečně
# nezatěžovali veřejný datový zdroj.
REQUEST_DELAY = 0.25
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2
PROJECT = "HVLK AI"
def utc_now():
    return datetime.now(timezone.utc).isoformat()
def fetch_json(url):
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
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            # Při omezení požadavků chvíli počkáme.
            if error.code == 429 or error.code >= 500:
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** (attempt + 1))
                    continue
            raise RuntimeError(last_error) from None
        except urllib.error.URLError as error:
            last_error = f"Chyba připojení: {error.reason}"
            if attempt < MAX_RETRIES:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(last_error) from None
        except json.JSONDecodeError:
            raise RuntimeError(
                "Zdroj vrátil neplatnou JSON odpověď."
            ) from None
    raise RuntimeError(last_error or "Neznámá chyba zdroje.")
def load_universe():
    if not UNIVERSE_FILE.exists():
        raise RuntimeError(
            "Chybí data/universe.json. "
            "Nejdříve spusť universe.py."
        )
    data = json.loads(
        UNIVERSE_FILE.read_text(encoding="utf-8")
    )
    instruments = data.get("results", [])
    if not instruments:
        raise RuntimeError(
            "Seznam instrumentů je prázdný."
        )
    # Odstraníme duplicity a prázdné symboly.
    unique = {}
    for item in instruments:
        symbol = str(item.get("symbol", "")).strip()
        if not symbol:
            continue
        key = symbol.upper()
        if key not in unique:
            unique[key] = {
                "symbol": symbol,
                "name": item.get("name") or symbol,
                "exchange": item.get("exchange"),
                "asset_type": item.get("asset_type", "stock"),
            }
    return list(unique.values())
def fetch_chart(symbol):
    # Historie přibližně jednoho roku kvůli dividendám.
    now = int(time.time())
    year_ago = now - 366 * 24 * 60 * 60
    params = {
        "period1": str(year_ago),
        "period2": str(now + 60),
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
    if chart.get("error"):
        raise RuntimeError(
            str(chart["error"])[:200]
        )
    results = chart.get("result") or []
    if not results:
        raise RuntimeError("Žádná cenová data.")
    return results[0]
def scan_symbol(instrument):
    symbol = instrument["symbol"]
    result = {
        **instrument,
        "price": None,
        "previous_close": None,
        "change_percent": None,
        "volume": None,
        "average_volume": None,
        "turnover_estimate": None,
        "currency": None,
        "market_cap": None,
        "dividend_per_share_year_estimate": None,
        "dividend_yield_percent_estimate": None,
        "dividend_observations": 0,
        "quote_timestamp": None,
        "status": "error",
        "notes": [],
    }
    try:
        data = fetch_chart(symbol)
        timestamps = data.get("timestamp") or []
        indicators = data.get("indicators") or {}
        quotes = indicators.get("quote") or []
        if not timestamps or not quotes:
            raise RuntimeError("Chybí cenová historie.")
        quote = quotes[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        valid = []
        for index, timestamp in enumerate(timestamps):
            if index >= len(closes):
                continue
            close = closes[index]
            if close is None:
                continue
            try:
                close = float(close)
            except (TypeError, ValueError):
                continue
            if close <= 0:
                continue
            volume = None
            if index < len(volumes) and volumes[index] is not None:
                try:
                    volume = int(volumes[index])
                except (TypeError, ValueError):
                    volume = None
            valid.append({
                "timestamp": int(timestamp),
                "close": close,
                "volume": volume,
            })
        if not valid:
            raise RuntimeError("Žádné platné závěrečné ceny.")
        latest = valid[-1]
        previous = valid[-2] if len(valid) >= 2 else None
        price = latest["close"]
        result["price"] = round(price, 6)
        result["currency"] = (
            (data.get("meta") or {}).get("currency")
        )
        result["quote_timestamp"] = latest["timestamp"]
        result["volume"] = latest["volume"]
        if previous:
            result["previous_close"] = round(
                previous["close"], 6
            )
            if previous["close"] > 0:
                result["change_percent"] = round(
                    (
                        price / previous["close"] - 1
                    ) * 100,
                    4,
                )
        recent_volumes = [
            item["volume"]
            for item in valid[-20:]
            if item["volume"] is not None
        ]
        if recent_volumes:
            result["average_volume"] = round(
                sum(recent_volumes) / len(recent_volumes)
            )
        # Obrat je odhad ceny poslední svíčky krát její objem.
        # Nejde o součet finančních obchodů z burzovního pásma.
        if latest["volume"] is not None:
            result["turnover_estimate"] = round(
                price * latest["volume"],
                2,
            )
        meta = data.get("meta") or {}
        # Yahoo chart metadata nemusí market cap poskytovat.
        market_cap = meta.get("marketCap")
        if market_cap is not None:
            result["market_cap"] = market_cap
        events = data.get("events") or {}
        dividends = events.get("dividends") or {}
        cutoff = int(time.time()) - 365 * 24 * 60 * 60
        dividend_values = []
        for timestamp, event in dividends.items():
            try:
                event_timestamp = int(timestamp)
                amount = float(event.get("amount", 0))
                if cutoff <= event_timestamp <= int(time.time()):
                    if amount > 0:
                        dividend_values.append(amount)
            except (TypeError, ValueError, AttributeError):
                continue
        annual_dividend = sum(dividend_values)
        result["dividend_per_share_year_estimate"] = round(
            annual_dividend,
            6,
        )
        result["dividend_observations"] = len(dividend_values)
        result["dividend_yield_percent_estimate"] = round(
            annual_dividend / price * 100,
            4,
        )
        result["status"] = "ok"
    except Exception as error:
        result["notes"].append(str(error)[:250])
    return result
def sort_by(results, field, reverse=True):
    available = [
        item for item in results
        if isinstance(item.get(field), (int, float))
    ]
    return sorted(
        available,
        key=lambda item: item[field],
        reverse=reverse,
    )
def make_market(results, universe_count):
    successful = [
        item for item in results
        if item["status"] == "ok"
    ]
    with_change = [
        item for item in successful
        if item["change_percent"] is not None
    ]
    with_volume = [
        item for item in successful
        if item["volume"] is not None
    ]
    with_turnover = [
        item for item in successful
        if item["turnover_estimate"] is not None
    ]
    # Neuvádíme extrémní procentní pohyby bez objemu
    # jako automaticky potvrzené obchodní příležitosti.
    liquid_movers = [
        item for item in with_change
        if (item.get("volume") or 0) > 0
    ]
    unusual_volume = []
    for item in successful:
        avg = item.get("average_volume")
        current = item.get("volume")
        if avg and current and avg > 0:
            ratio = current / avg
            if ratio >= 2:
                entry = dict(item)
                entry["volume_vs_average"] = round(ratio, 2)
                unusual_volume.append(entry)
    return {
        "project": PROJECT,
        "generated_at": utc_now(),
        "market": "US",
        "data_provider": "Yahoo Finance chart endpoint",
        "universe_symbols": universe_count,
        "scanned_symbols": len(results),
        "successful_quotes": len(successful),
        "failed_quotes": len(results) - len(successful),
        "coverage_percent": round(
            len(successful) / len(results) * 100, 2
        ) if results else 0,
        "top_gainers": sort_by(
            liquid_movers, "change_percent"
        )[:50],
        "top_losers": sort_by(
            liquid_movers, "change_percent", reverse=False
        )[:50],
        "top_volume": sort_by(
            with_volume, "volume"
        )[:50],
        "top_turnover": sort_by(
            with_turnover, "turnover_estimate"
        )[:50],
        "unusual_volume": sorted(
            unusual_volume,
            key=lambda item: item["volume_vs_average"],
            reverse=True,
        )[:50],
        "disclaimer": (
            "Data mohou být zpožděná nebo neúplná. "
            "Výsledky pokrývají pouze úspěšně načtené symboly. "
            "Změna ceny se počítá mezi posledními dvěma "
            "dostupnými denními závěrečnými cenami, nikoli "
            "nutně proti předchozímu obchodnímu dni. "
            "Obrat je odhad poslední ceny krát poslední "
            "dostupný denní objem. Dividendový výnos vychází "
            "z dostupných dividendových událostí za 365 dní "
            "a není předpovědí budoucích výplat. "
            "Nejde o investiční doporučení."
        ),
    }
def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    instruments = load_universe()
    total = len(instruments)
    print(f"HVLK AI: načteno symbolů: {total}")
    results = []
    for index, instrument in enumerate(instruments, start=1):
        symbol = instrument["symbol"]
        print(f"[{index}/{total}] {symbol}")
        item = scan_symbol(instrument)
        results.append(item)
        if item["status"] == "ok":
            print(
                f"  OK cena={item['price']} "
                f"změna={item['change_percent']}% "
                f"objem={item['volume']}"
            )
        else:
            print(
                f"  CHYBA: "
                f"{'; '.join(item['notes'])}"
            )
        if index < total:
            time.sleep(REQUEST_DELAY)
    successful = sum(
        1 for item in results
        if item["status"] == "ok"
    )
    # Pokud selže celý datový zdroj, skončíme chybou
    # místo toho, abychom zveřejnili prázdné výsledky.
    if successful == 0:
        raise RuntimeError(
            "Nepodařilo se získat cenu pro žádný symbol. "
            "Zkontroluj limity a dostupnost datového zdroje."
        )
    results.sort(
        key=lambda item: (
            item["dividend_yield_percent_estimate"] is not None,
            item["dividend_yield_percent_estimate"] or 0,
        ),
        reverse=True,
    )
    output = {
        "project": PROJECT,
        "generated_at": utc_now(),
        "data_provider": "Yahoo Finance chart endpoint",
        "scan_type": "US market universe",
        "universe_symbols": total,
        "scanned_symbols": len(results),
        "successful_quotes": successful,
        "failed_quotes": len(results) - successful,
        "coverage_percent": round(
            successful / len(results) * 100, 2
        ),
        "results": results,
        "disclaimer": (
            "Automatický orientační sken, nikoli investiční "
            "doporučení. Data mohou být zpožděná, neúplná "
            "nebo nedostupná. Pokrytí závisí na zdroji. "
            "Dividendový výnos je odhad z dostupných událostí "
            "za posledních 365 dní. Není zárukou budoucích "
            "výplat. Změna ceny se počítá z posledních dvou "
            "dostupných denních závěrečných cen."
        ),
    }
    market = make_market(results, total)
    RESULTS_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    MARKET_FILE.write_text(
        json.dumps(market, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("")
    print("========== HVLK AI SUMMARY ==========")
    print(f"Symbolů v seznamu: {total}")
    print(f"Úspěšně načtené ceny: {successful}")
    print(f"Chyby: {len(results) - successful}")
    print(f"Pokrytí: {output['coverage_percent']} %")
    print(f"Výsledky: {RESULTS_FILE}")
    print(f"Žebříčky: {MARKET_FILE}")
if __name__ == "__main__":
    main()
