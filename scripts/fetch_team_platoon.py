#!/usr/bin/env python3
"""
fetch_team_platoon.py
======================
REPLACES the old fetch_platoon_splits.py team-platoon logic (kept only its
pitcher-handedness cache function, which worked fine -- the platoon-split
fetch was the broken part).

WHY THE OLD SCRIPT WAS BROKEN: it relied on two Baseball Savant features
that don't actually exist for its use case, discovered via diagnostic dump
on 2026-07-22:
  1. `pitcher_throws={hand}` on /leaderboard/custom was silently ignored --
     identical byte-for-byte responses for hand=L and hand=R.
  2. `group_by=team` on both /leaderboard/custom and /statcast_search/csv
     does not aggregate to team level at all -- both endpoints kept
     returning player-level rows (player_id/player_name columns), which is
     why the script's team-column detection found nothing and silently
     discarded every row, every time, all season.

THIS SCRIPT instead pulls RAW PER-PITCH data (the same proven, validated
methodology as fetch_historical_daily_stats.py and
fetch_historical_battedball_stats.py) and aggregates team-level platoon
splits itself in Python, using only confirmed-real per-pitch columns:
  - inning_topbot + home_team/away_team -> which team is BATTING on this pitch
  - p_throws -> the opposing PITCHER's throwing hand (L/R)
  - woba_value/woba_denom/estimated_woba_using_speedangle -> wOBA/xwOBA,
    same methodology as every other script in this pipeline
  - events -> for K%/BB%
  - launch_speed/bb_type -> for hard_hit%

Pulls the full current season to date (regular season games only, via
hfGT=R|) rather than a rolling window, since platoon splits need a large
sample (only ~30% of PAs are vs LHP) and team-level batting tendency vs a
given handedness is stable day-to-day, unlike single-pitcher recent form.
Chunked into 5-day sub-requests per Savant's own query-performance guidance
-- a full-season pull is ~25-30 chunks, expect 12-18 minutes to run.
Because of that cost AND because team platoon tendency doesn't meaningfully
shift day to day, this is scheduled WEEKLY in daily_data.yml, not daily.

Output: data/team_platoon.csv (same schema the old script produced, so
log_games.py's load_platoon() needs no changes at all):
  team, pa_vs_lhp, xwoba_vs_lhp, bb_pct_vs_lhp, hard_hit_vs_lhp,
  off_score_vs_lhp, pa_vs_rhp, xwoba_vs_rhp, bb_pct_vs_rhp,
  hard_hit_vs_rhp, off_score_vs_rhp
"""

import csv, io, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

OUT_DIR   = Path("data")
OUT_FILE  = OUT_DIR / "team_platoon.csv"
TIMEOUT   = 45
CHUNK_DAYS = 5
SEASON_START = datetime(2026, 3, 1)  # safe wide bound; hfGT=R| already
                                      # restricts to actual regular-season
                                      # games, so an early start date is
                                      # harmless (no games exist before the
                                      # real season start anyway)
TODAY = datetime.utcnow().date()

LG_XWOBA = 0.318
LG_BB = 8.5
LG_HH = 37.0

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
    "player_type": "batter", "hfOuts": "", "opponent": "",
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
    cur = start.date()
    end_d = end
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


def calc_off_score(xwoba, bb_pct, hard_hit_pct):
    bb_norm = LG_XWOBA + (bb_pct - LG_BB) * 0.006
    hh_norm = LG_XWOBA + (hard_hit_pct - LG_HH) * 0.003
    return round(xwoba * 0.55 + bb_norm * 0.25 + hh_norm * 0.20, 4)


def main():
    chunks = list(daterange_chunks(SEASON_START, TODAY, CHUNK_DAYS))
    print(f"Season-to-date team platoon pull: {SEASON_START.date()} .. {TODAY} "
          f"({len(chunks)} chunks of {CHUNK_DAYS} days)\n")

    # key: (team, hand) -> aggregation dict
    agg = defaultdict(lambda: {
        "pa": 0, "k": 0, "bb": 0, "hbp": 0,
        "woba_num": 0.0, "woba_den": 0.0, "xwoba_num": 0.0,
        "batted_balls": 0, "hard_hit": 0,
    })

    total_pitch_rows = 0
    for i, (start, end) in enumerate(chunks, 1):
        print(f"  [{i}/{len(chunks)}] fetching {start} .. {end}")
        try:
            content = fetch_chunk(start, end)
        except Exception as e:
            print(f"  [SKIP] chunk {start}..{end} failed after retries, continuing "
                  f"with remaining chunks: {e}")
            continue

        reader = csv.DictReader(io.StringIO(content))
        n = 0
        for row in reader:
            n += 1
            total_pitch_rows += 1

            topbot = row.get("inning_topbot", "").strip()
            home_team = row.get("home_team", "").strip()
            away_team = row.get("away_team", "").strip()
            if not topbot or not (home_team and away_team):
                continue
            batting_team = away_team if topbot == "Top" else home_team

            p_throws = row.get("p_throws", "").strip()
            if p_throws not in ("L", "R"):
                continue

            events = row.get("events", "").strip()
            if not events:
                continue  # only care about PA-ending pitches here

            key = (batting_team, p_throws)
            a = agg[key]
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
    print(f"Unique (team, hand) buckets: {len(agg)}")

    if not agg:
        print("[FATAL] No data aggregated -- aborting without writing output.")
        sys.exit(1)

    teams = sorted(set(k[0] for k in agg.keys()))
    print(f"Teams found: {len(teams)}")

    PLATOON_FIELDS = [
        "team",
        "pa_vs_lhp", "xwoba_vs_lhp", "bb_pct_vs_lhp", "hard_hit_vs_lhp", "off_score_vs_lhp",
        "pa_vs_rhp", "xwoba_vs_rhp", "bb_pct_vs_rhp", "hard_hit_vs_rhp", "off_score_vs_rhp",
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=PLATOON_FIELDS)
        writer.writeheader()
        for team in teams:
            row = {"team": team}
            for hand, suffix in [("L", "lhp"), ("R", "rhp")]:
                a = agg.get((team, hand))
                if a is None or a["pa"] < 20:
                    # not enough sample -- fall back to league average rather
                    # than a wild/noisy small-sample number
                    row[f"pa_vs_{suffix}"] = a["pa"] if a else 0
                    row[f"xwoba_vs_{suffix}"] = LG_XWOBA
                    row[f"bb_pct_vs_{suffix}"] = LG_BB
                    row[f"hard_hit_vs_{suffix}"] = LG_HH
                    row[f"off_score_vs_{suffix}"] = calc_off_score(LG_XWOBA, LG_BB, LG_HH)
                    continue
                woba = a["woba_num"] / a["woba_den"] if a["woba_den"] else LG_XWOBA
                xwoba = a["xwoba_num"] / a["woba_den"] if a["woba_den"] else LG_XWOBA
                bb_pct = 100 * (a["bb"] + a["hbp"]) / a["pa"]
                hh_pct = 100 * a["hard_hit"] / a["batted_balls"] if a["batted_balls"] else LG_HH
                row[f"pa_vs_{suffix}"] = a["pa"]
                row[f"xwoba_vs_{suffix}"] = round(xwoba, 4)
                row[f"bb_pct_vs_{suffix}"] = round(bb_pct, 2)
                row[f"hard_hit_vs_{suffix}"] = round(hh_pct, 2)
                row[f"off_score_vs_{suffix}"] = calc_off_score(xwoba, bb_pct, hh_pct)
            writer.writerow(row)

    print(f"\n[OK] Wrote {len(teams)} teams to {OUT_FILE}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(OUT_DIR / "team_platoon_debug.txt", "w") as fp:
            fp.write("FAILED at " + datetime.utcnow().isoformat() + "\n\n")
            fp.write(traceback.format_exc())
        print("Wrote failure details to data/team_platoon_debug.txt")
        raise
