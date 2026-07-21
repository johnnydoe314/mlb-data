#!/usr/bin/env python3
"""
fetch_rolling_stats.py
=======================
Fetches ROLLING (last-N-days) pitcher stats from Baseball Savant's pitch-level
Statcast Search CSV export, and aggregates them into the same rate stats
fetch_stats.py already provides season-long (K%, BB%, wOBA, xwOBA, gap,
hard_hit%) -- but computed over only the last ROLLING_DAYS days.

WHY THIS EXISTS: our season-long "gap" (wOBA - xwOBA) is the core regression
signal behind R3/R4/R5 and sp_edge. It has repeatedly missed real, current
divergences this season -- Ober's rehab-start form, Bieber's actual-season
collapse vs his career track record, McGreevy's rolling 4-start FIP crisis --
because a season aggregate blends recent reality with stale early-season
performance. This script gives the same gap metric computed over a shorter,
more current window, to run alongside (not replace) the season number.

DATA SOURCE: Baseball Savant's /statcast_search/csv endpoint, which unlike the
/leaderboard/custom endpoint fetch_stats.py uses, supports arbitrary
game_date_gt/game_date_lt date-range filtering. This is the same endpoint and
parameter structure used by the well-established community libraries
`pybaseball` (statcast()) and `baseballr` (scrape_statcast_savant_pitcher_date()).

This is RAW PITCH-LEVEL data (one row per pitch), not pre-aggregated rates --
we compute K%, BB%, wOBA, xwOBA, and hard_hit% ourselves from the per-pitch
outcome columns, using the same methodology Baseball Savant itself uses for
season leaderboards:
  - wOBA  = sum(woba_value) / sum(woba_denom), over PA-ending pitches
  - xwOBA = same, but using estimated_woba_using_speedangle for batted-ball
            outcomes (walks/Ks/HBP already have a fixed linear-weight value,
            no "expected" version needed)
  - K%,BB% = strikeouts / PA,  walks+HBP / PA
  - hard_hit% = pitches with launch_speed >= 95 mph / batted-ball events

Per Baseball Savant's own documented guidance, wide date ranges risk timeouts
on this endpoint ("keep date ranges to 1-5 days for faster results" is the
guidance multiple community tools echo) -- so this script chunks the rolling
window into 5-day sub-requests and concatenates them, same pattern used by
baseballr's own multi-day scraper.

⚠️ THIS IS A NEW, UNTESTED DATA SOURCE as of 2026-07-21. The endpoint and
parameter structure are well-documented by independent community libraries,
but have not yet been live-validated against our specific pipeline. This
script should be run via manual_run.yml and its output inspected before it
is wired into log_games.py or trusted for any live signal.
"""

import csv, io, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

OUT_DIR    = Path("data")
OUT_FILE   = OUT_DIR / "rolling_stats.csv"
TIMEOUT    = 45
ROLLING_DAYS = 21          # ~4 starts for a 5-man rotation pitcher
CHUNK_DAYS   = 5           # per Savant's own query-performance guidance
MIN_PITCHES  = 20          # ignore pitchers with too few pitches in-window to be meaningful

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

# Base query params matching the community-documented statcast_search/csv
# structure (baseballr's scrape_statcast_savant_pitcher_date, pybaseball's
# statcast()). Empty-string params are required placeholders the backend
# expects even when unused -- omitting them has been reported to break
# the query in community implementations, so they are kept explicit here.
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


def daterange_chunks(days: int, chunk: int):
    end = datetime.utcnow().date()
    start = end - timedelta(days=days)
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(days=chunk), end)
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
    print(f"Fetching rolling ({ROLLING_DAYS}-day) pitcher stats from Baseball Savant...")
    print(f"Window: last {ROLLING_DAYS} days, chunked into {CHUNK_DAYS}-day sub-requests\n")

    all_rows = []
    chunks = list(daterange_chunks(ROLLING_DAYS, CHUNK_DAYS))
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
            all_rows.append(row)
            n += 1
        print(f"      -> {n} pitch rows")
        time.sleep(2)  # be polite between chunk requests

    print(f"\nTotal pitch-level rows across all chunks: {len(all_rows)}")
    if not all_rows:
        print("[FATAL] No data returned across any chunk -- aborting without writing output.")
        sys.exit(1)

    # ── Aggregate per pitcher ────────────────────────────────────────────
    agg = defaultdict(lambda: {
        "pitches": 0, "pa_ending": 0, "k": 0, "bb": 0, "hbp": 0,
        "woba_num": 0.0, "woba_den": 0.0,
        "xwoba_num": 0.0, "xwoba_den": 0.0,
        "batted_balls": 0, "hard_hit": 0,
        "name": "",
    })

    for row in all_rows:
        pid = row.get("pitcher", "").strip()
        if not pid:
            continue
        a = agg[pid]
        a["pitches"] += 1
        name = row.get("player_name", "").strip()
        if name:
            a["name"] = name

        events = row.get("events", "").strip()
        if events:  # this pitch ended a plate appearance
            a["pa_ending"] += 1
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

                # xwOBA: use estimated_woba_using_speedangle for batted balls;
                # fall back to the same fixed woba_value for non-batted-ball
                # PA endings (BB/HBP/K already have deterministic linear weights)
                xwoba_est = row.get("estimated_woba_using_speedangle", "").strip()
                if xwoba_est:
                    a["xwoba_num"] += f(xwoba_est) * woba_denom
                else:
                    a["xwoba_num"] += woba_value * woba_denom
                a["xwoba_den"] += woba_denom

            bb_type = row.get("bb_type", "").strip()
            if bb_type:  # a batted ball event
                a["batted_balls"] += 1
                ev = f(row.get("launch_speed"), None)
                if ev is not None and ev >= 95:
                    a["hard_hit"] += 1

    print(f"Unique pitchers seen: {len(agg)}")

    # ── Write output ──────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["pitcher_id", "name", "pitches", "pa", "k_percent", "bb_percent",
                  "woba", "xwoba", "gap", "hard_hit_percent", "window_days"]
    written = 0
    with open(OUT_FILE, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for pid, a in agg.items():
            if a["pitches"] < MIN_PITCHES or a["pa_ending"] == 0:
                continue
            pa = a["pa_ending"]
            k_pct = round(100 * a["k"] / pa, 1)
            bb_pct = round(100 * (a["bb"] + a["hbp"]) / pa, 1)
            woba = round(a["woba_num"] / a["woba_den"], 3) if a["woba_den"] else 0
            xwoba = round(a["xwoba_num"] / a["xwoba_den"], 3) if a["xwoba_den"] else 0
            gap = round(woba - xwoba, 3)
            hh_pct = round(100 * a["hard_hit"] / a["batted_balls"], 1) if a["batted_balls"] else 0
            writer.writerow({
                "pitcher_id": pid, "name": a["name"], "pitches": a["pitches"],
                "pa": pa, "k_percent": k_pct, "bb_percent": bb_pct,
                "woba": woba, "xwoba": xwoba, "gap": gap,
                "hard_hit_percent": hh_pct, "window_days": ROLLING_DAYS,
            })
            written += 1

    print(f"\n[OK] Wrote {written} pitchers (>={MIN_PITCHES} pitches in window) to {OUT_FILE}")


if __name__ == "__main__":
    main()
