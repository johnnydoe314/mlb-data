#!/usr/bin/env python3
"""
fetch_historical_daily_stats.py
================================
One-time historical pull: fetches pitch-level Statcast data for the full
season to date and aggregates it to PER-PITCHER-PER-DAY granularity (not a
single flat snapshot like fetch_rolling_stats.py produces).

WHY DAILY GRANULARITY: with per-day rows, we can compute the TRUE 21-day
rolling window that was in effect for ANY past game in game_log.csv, purely
by summing this file's rows over the right date range -- no need to make a
separate API call per historical game date. One pull, then arbitrary
backtesting locally.

Output: data/historical_daily_pitcher_stats.csv
  columns: pitcher_id, name, game_date, pitches, pa, k, bb, hbp,
           woba_num, woba_den, xwoba_num, batted_balls, hard_hit
  (rate stats are NOT pre-computed here -- summing these raw counting
  columns over any date range and dividing gives you the rate stats for
  that exact window, which is the whole point of keeping this granular.)

Same endpoint/methodology as fetch_rolling_stats.py (see that file's
docstring for the full explanation of the wOBA/xwOBA aggregation approach).
Chunked into 5-day sub-requests per Baseball Savant's own query-performance
guidance -- for a ~75-day historical range this means ~15 chunks, so this
takes meaningfully longer to run than the daily rolling fetch (expect
several minutes).

START_DATE defaults to 21 days before the earliest date we'd ever need a
rolling window for (2026-06-01, our first clean-sp_edge date per prior
analysis), giving every game in the backtest a full 21-day lookback even
for the earliest ones.
"""

import csv, io, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

OUT_DIR   = Path("data")
OUT_FILE  = OUT_DIR / "historical_daily_pitcher_stats.csv"
TIMEOUT   = 45
CHUNK_DAYS = 5

# Earliest game date we'd ever need a full 21-day lookback for, minus 21 days.
EARLIEST_NEEDED = datetime(2026, 6, 1) - timedelta(days=21)  # 2026-05-11
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


def daterange_chunks(start: datetime, end, chunk_days: int):
    cur = start.date() if isinstance(start, datetime) else start
    end_d = end if not isinstance(end, datetime) else end.date()
    while cur < end_d:
        chunk_end = min(cur + timedelta(days=chunk_days), end_d)
        yield cur.isoformat(), chunk_end.isoformat()
        cur = chunk_end


def f(v, default=0.0):
    try:
        if v in ("", None):
            return default
        return float(v)
    except Exception:
        return default


def main():
    chunks = list(daterange_chunks(EARLIEST_NEEDED, TODAY, CHUNK_DAYS))
    print(f"Historical pull: {EARLIEST_NEEDED.date()} .. {TODAY} "
          f"({len(chunks)} chunks of {CHUNK_DAYS} days)\n")

    # Per (pitcher_id, game_date) daily aggregation
    agg = defaultdict(lambda: {
        "pitches": 0, "pa": 0, "k": 0, "bb": 0, "hbp": 0,
        "woba_num": 0.0, "woba_den": 0.0, "xwoba_num": 0.0,
        "batted_balls": 0, "hard_hit": 0, "name": "",
    })

    total_pitch_rows = 0
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
            key = (pid, gdate)
            a = agg[key]
            a["pitches"] += 1
            name = row.get("player_name", "").strip()
            if name:
                a["name"] = name

            events = row.get("events", "").strip()
            if events:
                a["pa"] += 1
                if events == "strikeout":
                    a["k"] += 1
                elif events == "walk":
                    a["bb"] += 1
                elif events == "hit_by_pitch":
                    a["hbp"] += 1

                woba_denom = f(row.get("woba_denom"))
                woba_value = f(row.get("woba_value"))
                if woba_denom > 0:
                    a["woba_num"] += woba_value * woba_denom
                    a["woba_den"] += woba_denom
                    xwoba_est = row.get("estimated_woba_using_speedangle", "").strip()
                    if xwoba_est:
                        a["xwoba_num"] += f(xwoba_est) * woba_denom
                    else:
                        a["xwoba_num"] += woba_value * woba_denom

                bb_type = row.get("bb_type", "").strip()
                if bb_type:
                    a["batted_balls"] += 1
                    ev = f(row.get("launch_speed"), None)
                    if ev is not None and ev >= 95:
                        a["hard_hit"] += 1

        print(f"      -> {n} pitch rows (running total: {total_pitch_rows})")
        time.sleep(2)

    print(f"\nTotal pitch rows across all chunks: {total_pitch_rows}")
    print(f"Unique (pitcher, date) rows: {len(agg)}")

    if not agg:
        print("[FATAL] No data aggregated -- aborting without writing output.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["pitcher_id", "name", "game_date", "pitches", "pa", "k", "bb",
                  "hbp", "woba_num", "woba_den", "xwoba_num", "batted_balls", "hard_hit"]
    with open(OUT_FILE, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for (pid, gdate), a in sorted(agg.items(), key=lambda x: (x[0][1], x[0][0])):
            writer.writerow({
                "pitcher_id": pid, "name": a["name"], "game_date": gdate,
                "pitches": a["pitches"], "pa": a["pa"], "k": a["k"], "bb": a["bb"],
                "hbp": a["hbp"], "woba_num": round(a["woba_num"], 4),
                "woba_den": a["woba_den"], "xwoba_num": round(a["xwoba_num"], 4),
                "batted_balls": a["batted_balls"], "hard_hit": a["hard_hit"],
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
        with open(OUT_DIR / "historical_daily_stats_debug.txt", "w") as fp:
            fp.write("FAILED at " + datetime.utcnow().isoformat() + "\n\n")
            fp.write(traceback.format_exc())
        print("Wrote failure details to data/historical_daily_stats_debug.txt")
        raise
