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
    global _rate_limit_events

    started = now_utc()
    instruments = load_universe()
    total = len(instruments)
    print(
        f"HVLK AI: {total} symbolů; souběžnost {WORKERS}; "
        f"minimální interval {MIN_REQUEST_INTERVAL}s."
    )

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
                    "name": str(item.get("name", symbol))[:200],
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
    if _abort_event.is_set():
        raise SystemExit(
            "Sken byl zastaven kvůli opakovanému HTTP 429; "
            "výsledky jsou částečné a neměly by se publikovat jako kompletní."
        )


if __name__ == "__main__":
    main()
