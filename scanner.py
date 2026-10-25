#!/usr/bin/env python3
"""HVLK AI — US stock market scanner."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

UNIVERSE_FILE = DATA_DIR / "universe.json"
RESULTS_FILE = DATA_DIR / "results.json"
MARKET_FILE = DATA_DIR / "market.json"

WORKERS = max(
    1, min(int(os.getenv("HVLK_WORKERS", "8")), 8)
)
MIN_REQUEST_INTERVAL = max(
    0.1, float(os.getenv("HVLK_MIN_REQUEST_INTERVAL", "0.15"))
)
SAVE_EVERY = max(1, int(os.getenv("HVLK_SAVE_EVERY", "50")))
TOP_COUNT = max(
    1, min(int(os.getenv("HVLK_TOP_COUNT", "25")), 100)
)
REQUEST_TIMEOUT = max(
    5, int(os.getenv("HVLK_REQUEST_TIMEOUT", "20"))
)
MAX_RETRIES = max(
    0, min(int(os.getenv("HVLK_MAX_RETRIES", "3")), 6)
)

MAX_RESPONSE_BYTES = 5 * 1024 * 1024

_rate_lock = threading.Lock()
_last_request_at = 0.0

_rate_limit_lock = threading.Lock()
_rate_limit_events = 0
_abort_event = threading.Event()


# ============================================================
# UTILITIES
# ============================================================

def now_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically to avoid truncated files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    with temporary_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(temporary_path, path)


def normalise_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
