#!/usr/bin/env python3
"""
fetch_exit_velocity.py
======================
Downloads exit velocity / batted ball data from Baseball Savant.

Fetches two files:
  data/exit_velocity_batter.csv   — batter EV stats (avg speed, EV50, barrels, etc.)
  data/exit_velocity_pitcher.csv  — pitcher EV-against stats

Source: baseballsavant.mlb.com/leaderboard/statcast

Runs once daily at 10am CT via daily_data.yml.
"""

import csv, io, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("data")
YEAR    = 2026
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://baseballsavant.mlb.com/",
    "DNT": "1",
}

ENDPOINTS = {
    "batter":  f"https://baseballsavant.mlb.com/leaderboard/statcast?type=batter&year={YEAR}&position=&team=&min=q&csv=true",
    "pitcher": f"https://baseballsavant.mlb.com/leaderboard/statcast?type=pitcher&year={YEAR}&position=&team=&min=q&csv=true",
}

OUTPUT_FILES = {
    "batter":  OUT_DIR / "exit_velocity_batter.csv",
    "pitcher": OUT_DIR / "exit_velocity_pitcher.csv",
}


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} attempt {attempt}/{retries}: {e.reason}")
            if attempt < retries:
                time.sleep(attempt * 5)
            else:
                raise
        except Exception as e:
            print(f"  [!] Error attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                raise


def validate(content: str, player_type: str) -> int:
    reader = csv.reader(io.StringIO(content))
    hdrs = [h.strip().lower() for h in next(reader)]
    # Exit velocity data always has these columns
    required = {"player_id", "avg_hit_speed"} if "player_id" in " ".join(hdrs) \
               else {"last_name"}
    rows = sum(1 for _ in reader)
    return rows


def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 55)
    print(f"  FETCH EXIT VELOCITY — {ts}")
    print(f"  Year: {YEAR}  |  Min: qualified")
    print("=" * 55)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    success = 0

    for player_type, url in ENDPOINTS.items():
        out_path = OUTPUT_FILES[player_type]
        print(f"\n  [{player_type.upper()}] Fetching...", end="", flush=True)

        try:
            content = fetch(url)
            lines = content.strip().split("\n")
            rows = len(lines) - 1  # subtract header
            print(f" {rows} rows")

            if rows < 5:
                print(f"  [!] Suspiciously few rows — skipping save")
                print(f"  Preview: {lines[0][:100]}")
                continue

            out_path.write_text(content, encoding="utf-8")
            size_kb = out_path.stat().st_size / 1024
            print(f"  [✓] {out_path}  ({size_kb:.0f} KB)")

            # Show column headers
            hdrs = lines[0].split(",")[:8]
            print(f"  Cols: {', '.join(h.strip() for h in hdrs)}...")
            success += 1

        except Exception as e:
            print(f"\n  [FAIL] {e}")

        time.sleep(1)  # be polite between requests

    print(f"\n  Done: {success}/2 files saved")
    print("=" * 55)

    if success == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
