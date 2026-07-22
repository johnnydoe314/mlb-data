#!/usr/bin/env python3
"""
fetch_historical_battedball_stats.py
======================================
One-time historical pull: fetches pitch-level Statcast data for the full
season to date and aggregates BATTED-BALL TYPE breakdown (ground ball / fly
ball / line drive / popup) to PER-PITCHER-PER-DAY granularity, same
architecture as fetch_historical_daily_stats.py.

WHY: our existing signals (sp_edge, R3/R4, roll_kbb) are all built from
wOBA/xwOBA and K/BB rates -- none of them see a pitcher's underlying batted-
ball PROFILE. The LAD@PHI loss on 7/20 is the clean case study: Sheehan's
home-run problem (15 HR allowed in 82.1 IP, 24.5% ground-ball rate, lowest
on that day's slate) was invisible to our model, because none of our
existing metrics measure batted-ball type at all. External analysis with
batted-ball-aware data correctly favored Philadelphia; we didn't have the
data to see what they saw. This script is step one toward closing that gap.

DELIBERATELY NOT computing "barrel rate" in this pass -- MLB's official
Barrel classification is a non-trivial velocity/launch-angle matrix formula
that would need to be replicated exactly (or approximated) to be trustworthy,
and getting it subtly wrong would produce a misleading metric. Starting
with GB%/FB%/LD%/PU% instead: these are direct categorical classifications
from Statcast's own bb_type field, with zero approximation risk.

Output: data/historical_battedball_stats.csv
  columns: pitcher_id, name, game_date, batted_balls, ground_ball, fly_ball,
           line_drive, popup

Same endpoint/chunking/retry approach as fetch_historical_daily_stats.py.
"""

import csv, io, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

OUT_DIR   = Path("data")
OUT_FILE  = OUT_DIR / "historical_battedball_stats.csv"
TIMEOUT   = 45
CHUNK_DAYS = 5

EARLIEST_NEEDED = datetime(2026, 6, 1) - timedelta(days=21)  # 2026-05-11, matches
                                                              # the existing historical file's start
TODAY = datetime.utcnow().date()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://baseballsavant.mlb.com/statcast_search",
    "DNT":             "1",
}

BASE_PARAMS = {
    "all": "true", "hfPT": "", "hfAB": "", "hfBBT": "", "hfPR": "",
    "hfZ": "", "stadium": "", "hfBBL": "", "hfNewZones": "",
    "hfGT": "R|", "hfC": "", "hfSea": "2026|", "hfSit": "",
    "player_type": "pitcher", "hfOuts": "", "opponent": "",
    "pitcher_throws": "", "batter_stands": "", "hfSA": "",
    "hfInfield": "", "team": "", "position": "", "hfOutfield": "",
    "hfRO": "", "home_road": "", "hfFlag": "", "hfPull": "",
    "metric_1": "", "hfInn": "", "min_pitches": "0", "min_results": "0",
    "group_by": "name", "sort_col": "pitches",
    "player_event_sort": "h_launch_speed", "sort_order": "desc",
    "min_abs": "0", "type": "details",
}


def build_url(start_date: str, end_date: str) -> str:
    params = dict(BASE_PARAMS)
    params["game_date_gt"] = start_date
    params["game_date_lt"] = end_date
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"https://baseballsavant.mlb.com/statcast_search/csv?{query}"


def fetch_chunk(start_date: str, end_date: str, retries: int = 3) -> str:
    url = build_url(start_date, end_date)
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} on {start_date}..{end_date}, attempt {attempt}/{retries}: {e.reason}")
            if e.code in (403, 429) and attempt < retries:
                time.sleep(attempt * 10)
            else:
                raise
        except Exception as e:
            print(f"  [!] Error on {start_date}..{end_date}, attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                raise


def daterange_chunks(start, end, chunk_days: int):
    cur = start.date() if isinstance(start, datetime) else start
    end_d = end if not isinstance(end, datetime) else end.date()
    while cur < end_d:
        chunk_end = min(cur + timedelta(days=chunk_days), end_d)
        yield cur.isoformat(), chunk_end.isoformat()
        cur = chunk_end


def main():
    chunks = list(daterange_chunks(EARLIEST_NEEDED, TODAY, CHUNK_DAYS))
    print(f"Historical batted-ball pull: {EARLIEST_NEEDED.date()} .. {TODAY} "
          f"({len(chunks)} chunks of {CHUNK_DAYS} days)\n")

    agg = defaultdict(lambda: {
        "batted_balls": 0, "ground_ball": 0, "fly_ball": 0,
        "line_drive": 0, "popup": 0, "name": "",
    })

    total_pitch_rows = 0
    total_batted = 0
    for i, (start, end) in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] fetching {start} .. {end}")
        try:
            content = fetch_chunk(start, end)
        except Exception as e:
            print(f"  [FATAL] chunk {start}..{end} failed after retries: {e}")
            sys.exit(1)

        reader = csv.DictReader(io.StringIO(content))
        n = 0
        for row in reader:
            n += 1
            total_pitch_rows += 1
            pid = row.get("pitcher", "").strip()
            gdate = row.get("game_date", "").strip()
            if not pid or not gdate:
                continue
            bb_type = row.get("bb_type", "").strip()
            if not bb_type:
                continue  # only care about actual batted-ball events here

            key = (pid, gdate)
            a = agg[key]
            name = row.get("player_name", "").strip()
            if name:
                a["name"] = name
            a["batted_balls"] += 1
            total_batted += 1
            if bb_type == "ground_ball":
                a["ground_ball"] += 1
            elif bb_type == "fly_ball":
                a["fly_ball"] += 1
            elif bb_type == "line_drive":
                a["line_drive"] += 1
            elif bb_type == "popup":
                a["popup"] += 1

        print(f"      -> {n} pitch rows (running total: {total_pitch_rows}, "
              f"batted balls so far: {total_batted})")
        time.sleep(2)

    print(f"\nTotal pitch rows across all chunks: {total_pitch_rows}")
    print(f"Total batted-ball events: {total_batted}")
    print(f"Unique (pitcher, date) rows: {len(agg)}")

    if not agg:
        print("[FATAL] No data aggregated -- aborting without writing output.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["pitcher_id", "name", "game_date", "batted_balls",
                  "ground_ball", "fly_ball", "line_drive", "popup"]
    with open(OUT_FILE, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for (pid, gdate), a in sorted(agg.items(), key=lambda x: (x[0][1], x[0][0])):
            writer.writerow({
                "pitcher_id": pid, "name": a["name"], "game_date": gdate,
                "batted_balls": a["batted_balls"], "ground_ball": a["ground_ball"],
                "fly_ball": a["fly_ball"], "line_drive": a["line_drive"],
                "popup": a["popup"],
            })

    print(f"\n[OK] Wrote {len(agg)} pitcher-day rows to {OUT_FILE}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / "historical_battedball_debug.txt", "w") as fp:
            fp.write("FAILED at " + datetime.utcnow().isoformat() + "\n\n")
            fp.write(traceback.format_exc())
        print("Wrote failure details to data/historical_battedball_debug.txt")
        raise
