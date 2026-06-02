#!/usr/bin/env python3
"""
fetch_stats.py
==============
Fetches pitcher Statcast leaderboard from Baseball Savant and saves stats.csv
Simple, focused, minimal dependencies — designed to run reliably in GitHub Actions.
"""

import csv
import io
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

YEARS       = "2026,2025,2024,2023"
OUT_DIR     = Path("data")
OUT_FILE    = OUT_DIR / "stats.csv"
META_FILE   = OUT_DIR / "metadata.json"
MIN_PA      = 50
TIMEOUT     = 30

SAVANT_URL = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    f"?year={YEARS.replace(',', '%2C')}"
    f"&type=pitcher&filter=&min={MIN_PA}"
    "&selections=pa%2Ck_percent%2Cbb_percent%2Cwoba%2Cxwoba"
    "%2Csweet_spot_percent%2Cbarrel_batted_rate%2Chard_hit_percent"
    "%2Cavg_best_speed%2Cavg_hyper_speed%2Cwhiff_percent%2Cswing_percent"
    "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm"
    "&sort=xwoba&sortDir=asc&csv=true"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://baseballsavant.mlb.com/",
    "DNT":             "1",
}

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} on attempt {attempt}/{retries}: {e.reason}")
            if e.code in (403, 429) and attempt < retries:
                wait = attempt * 10
                print(f"  ... waiting {wait}s before retry")
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            print(f"  [!] Error on attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                raise

# ── Validate ──────────────────────────────────────────────────────────────────

def validate(content: str) -> int:
    reader = csv.reader(io.StringIO(content))
    headers = [h.strip().lower() for h in next(reader)]
    required = {"woba", "xwoba", "k_percent", "hard_hit_percent"}
    missing  = required - set(headers)
    if missing:
        raise ValueError(f"Missing expected columns: {missing}")
    rows = sum(1 for _ in reader)
    return rows

# ── Metadata ──────────────────────────────────────────────────────────────────

def write_meta(rows: int, ok: bool):
    import json
    meta = {
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "years":        YEARS,
        "pitcher_rows": rows,
        "status":       "ok" if ok else "error",
    }
    META_FILE.write_text(json.dumps(meta, indent=2))

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print(f"  FETCH STATS.CSV — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Years: {YEARS}  |  Min PA: {MIN_PA}")
    print("=" * 55)

    # Ensure output dir exists
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Fetch
    print("\n  Fetching from Baseball Savant...", end="", flush=True)
    try:
        content = fetch(SAVANT_URL)
    except Exception as e:
        print(f"\n  [FAIL] Could not fetch data: {e}")
        write_meta(0, ok=False)
        sys.exit(1)

    # Validate
    try:
        rows = validate(content)
        print(f" {rows} pitchers loaded.")
    except Exception as e:
        print(f"\n  [FAIL] Validation error: {e}")
        print(f"  First 200 chars of response: {content[:200]}")
        write_meta(0, ok=False)
        sys.exit(1)

    # Save
    OUT_FILE.write_text(content, encoding="utf-8")
    size = OUT_FILE.stat().st_size
    print(f"  [✓] Saved: {OUT_FILE}  ({size/1024:.0f} KB)")

    write_meta(rows, ok=True)

    print(f"\n  Done.")
    print("=" * 55)

if __name__ == "__main__":
    main()
