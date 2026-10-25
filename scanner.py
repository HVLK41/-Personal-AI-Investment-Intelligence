            "HTTP 404 means data was unavailable for the request.",
            "HTTP 429 means the provider is rate-limiting requests.",
        ],
    }


# ============================================================
# SAVE RESULTS
# ============================================================

def save_all(
    total: int,
    rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    started: str,
) -> None:
    write_json(
        RESULTS_FILE,
        {
            "project": "HVLK AI",
            "generated_at": now_utc(),
            "market": "US",
            "data_provider": "Yahoo Finance chart endpoint",
            "total_symbols": total,
            "successful_quotes": len(rows),
            "failed_quotes": len(errors),
            "coverage_percent": (
                round(len(rows) / total * 100, 2)
                if total
                else 0
            ),
            "results": rows,
            "errors": errors[:500],
        },
    )

    write_json(
        MARKET_FILE,
        build_market(total, rows, errors, started),
    )


# ============================================================
# MAIN SCAN
# ============================================================

def main() -> None:
    global _rate_limit_events

    started = now_utc()
    instruments = load_universe()
    total = len(instruments)

    _rate_limit_events = 0
    _abort_event.clear()

    print(
        f"HVLK AI: {total} symbols; "
        f"workers={WORKERS}; "
        f"minimum request interval={MIN_REQUEST_INTERVAL}s.",
        flush=True,
    )

    rows_by_symbol: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    completed = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_quote, instrument): instrument
            for instrument in instruments
        }

        for future in as_completed(futures):
            instrument = futures[future]
            symbol = instrument["symbol"]
            completed += 1

            try:
                row, error = future.result()
            except Exception as exc:
                row = None
                error = f"{type(exc).__name__}: {exc}"

            if row is not None:
                rows_by_symbol[symbol] = row
            else:
                errors.append(
                    {
                        "symbol": symbol,
                        "name": str(
                            instrument.get("name") or symbol
                        )[:200],
                        "error": error or "Unknown error",
                    }
                )

            if completed % 25 == 0 or completed == total:
                print(
                    f"{completed}/{total} | "
                    f"successful={len(rows_by_symbol)} | "
                    f"errors={len(errors)}",
                    flush=True,
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

    # Always save final state, including a partial run.
    save_all(total, current_rows, errors, started)
    summary = build_market(total, current_rows, errors, started)

    print("\n--- HVLK AI SUMMARY ---")
    print(f"Successful quotes: {len(current_rows)}")
    print(f"Errors: {len(errors)}")
    print(f"Coverage: {summary['coverage_percent']}%")
    print(f"Errors by type: {summary['errors_by_type']}")

    if _abort_event.is_set():
        raise SystemExit(
            "Scan stopped after repeated HTTP 429. "
            "Results are partial; do not publish them as complete."
        )


if __name__ == "__main__":
    main()
