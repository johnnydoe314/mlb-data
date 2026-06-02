#!/usr/bin/env python3
"""
fetch_stats.py — updated
========================
Fetches pitcher Statcast leaderboard from Baseball Savant.
Updated URL: min=30 PA (was 50), expanded to 46 metrics including
swing speed, contact quality, expected stats, and standard slash line.

min=30 also captures more relievers, improving bullpen data coverage.
"""

import csv, io, os, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR   = Path("data")
OUT_FILE  = OUT_DIR / "stats.csv"
META_FILE = OUT_DIR / "metadata.json"
TIMEOUT   = 30

# Full URL matching the user's custom leaderboard
# min=30 captures starters AND regular relievers
SAVANT_URL = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    "?year={years}"
    "&type=pitcher&filter=&min=30"
    "&selections="
    "player_age,p_game,p_formatted_ip,"
    "pa,ab,hit,single,double,triple,home_run,strikeout,walk,"
    "k_percent,bb_percent,"
    "batting_avg,slg_percent,on_base_percent,on_base_plus_slg,"
    "p_run_support,"
    "xba,xslg,woba,xwoba,xobp,xiso,"
    "avg_swing_speed,fast_swing_rate,"
    "blasts_contact,blasts_swing,"
    "squared_up_contact,squared_up_swing,"
    "avg_swing_length,swords,"
    "attack_angle,attack_direction,ideal_angle_rate,vertical_swing_path,"
    "exit_velocity_avg,launch_angle_avg,"
    "sweet_spot_percent,barrel_batted_rate,hard_hit_percent,"
    "avg_best_speed,avg_hyper_speed,"
    "whiff_percent,swing_percent"
    "&chart=false&x=player_age&y=player_age&r=no&chartType=beeswarm"
    "&sort=xwoba&sortDir=asc&csv=true"
)

YEARS = "2026,2025,2024,2023"

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


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} on attempt {attempt}/{retries}: {e.reason}")
            if e.code in (403, 429) and attempt < retries:
                time.sleep(attempt * 10)
            else:
                raise
        except Exception as e:
            print(f"  [!] Error on attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                raise


def validate(content: str) -> int:
    reader = csv.reader(io.StringIO(content))
    headers = [h.strip().lower() for h in next(reader)]
    required = {"woba", "xwoba", "k_percent", "hard_hit_percent"}
    missing  = required - set(headers)
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    return sum(1 for _ in reader)


def main():
    fetched_at = datetime.utcnow().isoformat() + "Z"
    print("=" * 58)
    print(f"  FETCH STATS — {fetched_at[:19]}")
    print(f"  Years: {YEARS}  |  Min PA: 30  |  Metrics: 46")
    print("=" * 58)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    url = SAVANT_URL.format(years=YEARS.replace(",", "%2C"))

    print("\n  Fetching from Baseball Savant...", end="", flush=True)
    try:
        content = fetch(url)
    except Exception as e:
        print(f"\n  [FAIL] {e}")
        sys.exit(1)

    try:
        rows = validate(content)
        print(f" {rows} pitcher rows loaded.")
    except Exception as e:
        print(f"\n  [FAIL] {e}")
        print(f"  First 200 chars: {content[:200]}")
        sys.exit(1)

    OUT_FILE.write_text(content, encoding="utf-8")
    size = OUT_FILE.stat().st_size
    print(f"  [✓] Saved: {OUT_FILE}  ({size/1024:.0f} KB)")

    import json
    meta = {
        "last_updated":  fetched_at,
        "years":         YEARS,
        "min_pa":        30,
        "metrics_count": 46,
        "pitcher_rows":  rows,
        "status":        "ok",
    }
    META_FILE.write_text(json.dumps(meta, indent=2))
    print(f"  [✓] Metadata updated")
    print(f"\n  Done. {rows} pitchers across {YEARS}.")
    print("=" * 58)


if __name__ == "__main__":
    main()
