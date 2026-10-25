import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UNIVERSE_FILE = DATA_DIR / "universe.json"
RESULTS_FILE = DATA_DIR / "results.json"
MARKET_FILE = DATA_DIR / "market.json"
# Konzervativní souběžnost: rychlejší než sekvenční skenování,
# ale méně agresivní vůči veřejnému datovému zdroji.
WORKERS = 5
TIMEOUT_SECONDS = 20
RETRIES = 2
SAVE_EVERY = 100
TOP_COUNT = 50
USER_AGENT = "HVLK-AI/1.0 (personal market research)"
def utc_now():
    return datetime.now(timezone.utc).isoformat()
def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
def load_universe():
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(
            "Chybí data/universe.json. Nejdřív spusť universe.py."
        )
    data = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
    symbols = data.get("results", [])
    if not isinstance(symbols, list) or not symbols:
        raise RuntimeError("Seznam akcií je prázdný nebo má neplatný formát.")
    # Odstranění duplicit bez změny původního pořadí.
    unique = {}
    for item in symbols:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).strip().upper()
        if symbol and symbol not in unique:
            item["symbol"] = symbol
            unique[symbol] = item
    return data, list(unique.values())
def yahoo_symbol(symbol):
    # Yahoo obvykle používá pomlčku místo tečky, např. BRK.B -> BRK-B.
    return symbol.replace(".", "-")
def request_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))
def fetch_quote(instrument):
    symbol = instrument["symbol"]
    provider_symbol = yahoo_symbol(symbol)
    encoded_symbol = urllib.parse.quote(provider_symbol, safe="-^=")
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{encoded_symbol}?range=1y&interval=1d&events=div%2Csplits"
    )
    last_error = "Neznámá chyba"
    for attempt in range(RETRIES + 1):
        try:
            payload = request_json(url)
            chart = payload.get("chart", {})
            chart_error = chart.get("error")
            if chart_error:
                description = chart_error.get("description", "Chyba Yahoo Finance")
                return None, f"Yahoo: {description}"
            chart_results = chart.get("result") or []
            if not chart_results:
                return None, "Yahoo nevrátilo cenová data"
            result = chart_results[0]
            timestamps = result.get("timestamp") or []
            indicators = result.get("indicators") or {}
            quote_groups = indicators.get("quote") or []
            if not timestamps or not quote_groups:
                return None, "Chybí historie cen nebo objemu"
            quote = quote_groups[0]
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []
            observations = []
            for index, timestamp in enumerate(timestamps):
                close = closes[index] if index < len(closes) else None
                volume = volumes[index] if index < len(volumes) else None
                if close is None:
                    continue
                try:
                    close = float(close)
                    if close <= 0:
                        continue
                except (ValueError, TypeError):
                    continue
                try:
                    volume = int(volume or 0)
                except (ValueError, TypeError):
                    volume = 0
                observations.append({
                    "timestamp": int(timestamp),
                    "close": close,
                    "volume": max(volume, 0),
                })
            if not observations:
                return None, "Žádné použitelné denní ceny"
            latest = observations[-1]
            previous = observations[-2] if len(observations) >= 2 else None
            change_percent = None
            if previous and previous["close"]:
                change_percent = (
                    (latest["close"] - previous["close"])
                    / previous["close"]
                    * 100
                )
            # Průměr objemu za posledních 20 dostupných pozorování.
            volume_history = [
                row["volume"]
                for row in observations[-20:]
                if row["volume"] > 0
            ]
            average_volume = (
                sum(volume_history) / len(volume_history)
                if volume_history else None
            )
            turnover = latest["close"] * latest["volume"]
            meta = result.get("meta") or {}
            currency = meta.get("currency") or "USD"
            dividend_events = (result.get("events") or {}).get("dividends") or {}
            cutoff = latest["timestamp"] - 365 * 24 * 60 * 60
            dividend_total = 0.0
            dividend_count = 0
            for event in dividend_events.values():
                try:
                    event_date = int(event.get("date", 0))
                    amount = float(event.get("amount", 0))
                    if event_date >= cutoff and amount > 0:
                        dividend_total += amount
                        dividend_count += 1
                except (ValueError, TypeError, AttributeError):
                    continue
            dividend_yield = (
                dividend_total / latest["close"] * 100
                if latest["close"] > 0 else None
            )
            return {
                "symbol": symbol,
                "name": instrument.get("name") or symbol,
                "exchange": instrument.get("exchange") or "Unknown",
                "asset_type": "stock",
                "price": round(latest["close"], 6),
                "previous_close": round(previous["close"], 6) if previous else None,
                "change_percent": round(change_percent, 4) if change_percent is not None else None,
                "volume": latest["volume"],
                "average_volume": round(average_volume, 2) if average_volume is not None else None,
                "turnover_estimate": round(turnover, 2),
                "currency": currency,
                "market_cap": meta.get("marketCap"),
                "dividend_per_share_year_estimate": round(dividend_total, 6),
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
            if error.code not in (408, 425, 429, 500, 502, 503, 504):
                return None, last_error
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
        ) as error:
            last_error = f"{type(error).__name__}: {error}"
        if attempt < RETRIES:
            time.sleep((2 ** attempt) + random.uniform(0.2, 1.0))
    return None, last_error
def rank_rows(rows, key, reverse=True):
    valid = [row for row in rows if row.get(key) is not None]
    return sorted(valid, key=lambda row: row[key], reverse=reverse)[:TOP_COUNT]
def build_market_data(universe_data, rows, errors, started_at):
    valid_changes = [
        row for row in rows
        if row.get("change_percent") is not None
    ]
    unusual_volume = []
    for row in rows:
        current = row.get("volume")
        average = row.get("average_volume")
        if current is None or not average or average <= 0:
            continue
        ratio = current / average
        if ratio >= 2:
            item = dict(row)
            item["volume_ratio"] = round(ratio, 2)
            unusual_volume.append(item)
    unusual_volume.sort(
        key=lambda row: row.get("volume_ratio", 0),
        reverse=True,
    )total = universe_data if isinstance(universe_data, int) else len(universe_data)
    success = len(rows)
    failure = len(errors)
    return {
        "project": "HVLK AI",
        "generated_at": utc_now(),
        "market": "US",
        "asset_scope": "US stocks only; actual instrument classification depends on universe filtering",
        "data_provider": "Yahoo Finance chart endpoint",
        "universe_symbols": total,
        "scanned_symbols": success + failure,
        "successful_quotes": success,
        "failed_quotes": failure,
        "coverage_percent": round(success / total * 100, 2) if total else 0,
        "scan_started_at": started_at,
        "scan_finished_at": utc_now(),
        "top_gainers": rank_rows(valid_changes, "change_percent", True),
        "top_losers": rank_rows(valid_changes, "change_percent", False),
        "highest_volume": rank_rows(rows, "volume", True),
        "highest_turnover": rank_rows(rows, "turnover_estimate", True),
        "unusual_volume": unusual_volume[:TOP_COUNT],
        "errors_by_type": count_error_types(errors),
        "sample_errors": errors[:50],
        "data_notes": [
            "Změna ceny porovnává poslední dvě dostupné denní závěrečné ceny.",
            "Mimo obchodní hodiny může jít o poslední dostupný obchodní den, nikoliv dnešní změnu.",
            "Finanční obrat je odhad: poslední cena × poslední denní objem.",
            "Průměrný objem zahrnuje posledních až 20 dostupných denních pozorování včetně posledního dne.",
            "Dividendové údaje jsou odhady odvozené z událostí vrácených poskytovatelem.",
            "Neúspěšné symboly nejsou automaticky důkazem, že akcie neexistuje.",
        ],
    }
def count_error_types(errors):
    counts = {}
    for item in errors:
        error = item.get("error", "Neznámá chyba")
        key = error.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return counts
def main():
    started_at = utc_now()
    universe_data, instruments = load_universe()
    total = len(instruments)
    print(f"HVLK AI: začíná skenování {total} symbolů.")
    print(f"Počet souběžných požadavků: {WORKERS}")
    results_by_symbol = {}
    errors = []
    completed = 0
    # Ukládáme průběžné výsledky, aby při přerušení nebylo vše ztraceno.
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {
            executor.submit(fetch_quote, instrument): instrument
            for instrument in instruments
        }
        for future in as_completed(future_map):
            instrument = future_map[future]
            symbol = instrument["symbol"]
            completed += 1
            try:
                result, error = future.result()
            except Exception as exception:
                result = None
                error = f"{type(exception).__name__}: {exception}"
            if result:
                results_by_symbol[symbol] = result
            else:
                errors.append({
                    "symbol": symbol,
                    "name": instrument.get("name", symbol),
                    "error": error or "Neznámá chyba",
                })
            if completed % 25 == 0 or completed == total:
                print(
                    f"Průběh: {completed}/{total} | "
                    f"úspěšné: {len(results_by_symbol)} | "
                    f"chyby: {len(errors)}"
                )
            if completed % SAVE_EVERY == 0 or completed == total:
                rows = sorted(
                    results_by_symbol.values(),
                    key=lambda item: item["symbol"],
                )
                results_payload = {
                    "project": "HVLK AI",
                    "generated_at": utc_now(),
                    "market": "US",
                    "data_provider": "Yahoo Finance chart endpoint",
                    "total_symbols": total,
                    "successful_quotes": len(rows),
                    "failed_quotes": len(errors),
                    "coverage_percent": round(
                        len(rows) / total * 100, 2
                    ) if total else 0,
                    "results": rows,
                    "errors": errors[:500],
                }
                write_json(RESULTS_FILE, results_payload)
                market_payload = build_market_data(
                    total, rows, errors, started_at
                )
                write_json(MARKET_FILE, market_payload)
    rows = sorted(
        results_by_symbol.values(),
        key=lambda item: item["symbol"],
    )
    results_payload = {
        "project": "HVLK AI",
        "generated_at": utc_now(),
        "market": "US",
        "data_provider": "Yahoo Finance chart endpoint",
        "total_symbols": total,
        "successful_quotes": len(rows),
        "failed_quotes": len(errors),
        "coverage_percent": round(len(rows) / total * 100, 2) if total else 0,
        "results": rows,
        "errors": errors[:500],
    }
    market_payload = build_market_data(total, rows, errors, started_at)
    write_json(RESULTS_FILE, results_payload)
    write_json(MARKET_FILE, market_payload)
    print("\n--- SOUHRN HVLK AI ---")
    print(f"Symboly v seznamu: {total}")
    print(f"Úspěšná data: {len(rows)}")
    print(f"Chyby: {len(errors)}")
    print(f"Pokrytí: {market_payload['coverage_percent']} %")
    print(f"Výstup: {RESULTS_FILE}")
    print(f"Výstup: {MARKET_FILE}")
    if errors:
        print("Nejčastější typy chyb:")
        for error_type, count in sorted(
            market_payload["errors_by_type"].items(),
            key=lambda item: item[1],
            reverse=True,
        ):
            print(f"  {error_type}: {count}")
if __name__ == "__main__":
    main()
