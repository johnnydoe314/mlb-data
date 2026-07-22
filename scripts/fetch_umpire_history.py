#!/usr/bin/env python3
"""
fetch_umpire_history.py
========================
One-time historical pull: for every MLB game since 2026-06-01, records the
home plate umpire and the game's actual runs/walks/strikeouts totals, using
MLB's official Stats API (statsapi.mlb.com) -- confirmed via umpire_diagnostic.py
that game/{gamePk}/boxscore returns an "officials" list with
{official: {id, fullName}, officialType} entries, where officialType is one
of "Home Plate", "First Base", "Second Base", "Third Base".

WHY THIS EXISTS: home plate umpires have real, measurable strike-zone-size
tendencies (well-established by UmpScorecards, FanGraphs, and academic
umpire-analytics research) that shift the scoring environment independent of
the two teams' true talent -- a "hitter's zone" umpire increases walks and
scoring; a "pitcher's zone" umpire does the opposite. Our composite currently
has no umpire input at all.

APPROACH: (1) pull the schedule for the date range to get every gamePk,
(2) for each gamePk, fetch the boxscore, extract the HP umpire and the
combined game totals (runs, walks, strikeouts), (3) write one row per game
to data/umpire_game_history.csv. This is intentionally kept at the raw
per-game level (not pre-aggregated per umpire) so that any later analysis
can compute a proper BEFORE-this-game rolling/season tendency per umpire
without lookahead bias, the same discipline used for the pitcher rolling
stats.

SCALE NOTE: unlike the Baseball Savant pulls (which return many games per
request), this requires ONE API call per game -- roughly 800+ calls for a
full June-onward season. Paced with a short delay between calls to be a
polite API citizen. Expect this to take significantly longer to run than
the other fetch scripts.

⚠️ NEW, UNTESTED AT SCALE as of 2026-07-22. Diagnostic-validated against 3
sample games; this is the first full run.
"""

import csv, json, sys, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path

OUT_DIR = Path("data")
OUT_FILE = OUT_DIR / "umpire_game_history.csv"
DEBUG_FILE = OUT_DIR / "umpire_history_debug.txt"

START_DATE = "2026-06-01"
END_DATE = datetime.utcnow().date().isoformat()

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
REQUEST_DELAY = 0.35   # seconds between boxscore calls, to be a polite citizen
TIMEOUT = 20


def fetch_json(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < retries:
                time.sleep(attempt * 5)
                continue
            raise
        except Exception:
            if attempt < retries:
                time.sleep(3)
                continue
            raise


def get_schedule_game_pks(start_date, end_date):
    """Returns list of (gamePk, gameDate, awayTeam, homeTeam) for regular-season games."""
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&gameType=R&startDate={start_date}&endDate={end_date}")
    data = fetch_json(url)
    games = []
    for date_entry in data.get("dates", []):
        game_date = date_entry.get("date", "")
        for g in date_entry.get("games", []):
            status = g.get("status", {}).get("codedGameState", "")
            # Only completed games (F=final, O=game over). Skip postponed/scheduled.
            if status not in ("F", "O"):
                continue
            games.append({
                "game_pk": g.get("gamePk"),
                "game_date": game_date,
                "away_team": g.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation", ""),
                "home_team": g.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation", ""),
            })
    return games


def get_hp_umpire_and_totals(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    data = fetch_json(url)

    hp_ump_id, hp_ump_name = None, None
    for o in data.get("officials", []):
        if o.get("officialType") == "Home Plate":
            hp_ump_id = o.get("official", {}).get("id")
            hp_ump_name = o.get("official", {}).get("fullName")
            break

    away_pitching = data.get("teams", {}).get("away", {}).get("teamStats", {}).get("pitching", {})
    home_pitching = data.get("teams", {}).get("home", {}).get("teamStats", {}).get("pitching", {})
    # away team's runs allowed = home team's runs scored, and vice versa, but
    # simpler/more robust to read each side's own "batting" runs for scored totals
    away_batting = data.get("teams", {}).get("away", {}).get("teamStats", {}).get("batting", {})
    home_batting = data.get("teams", {}).get("home", {}).get("teamStats", {}).get("batting", {})

    return {
        "hp_umpire_id": hp_ump_id,
        "hp_umpire_name": hp_ump_name,
        "away_runs": away_batting.get("runs"),
        "home_runs": home_batting.get("runs"),
        "away_bb": away_batting.get("baseOnBalls"),
        "home_bb": home_batting.get("baseOnBalls"),
        "away_k": away_batting.get("strikeOuts"),
        "home_k": home_batting.get("strikeOuts"),
    }


def main():
    print(f"Pulling schedule {START_DATE} .. {END_DATE} ...")
    games = get_schedule_game_pks(START_DATE, END_DATE)
    print(f"Found {len(games)} completed regular-season games.\n")

    if not games:
        print("[FATAL] No games found -- aborting.")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["game_pk", "game_date", "away_team", "home_team",
                  "hp_umpire_id", "hp_umpire_name",
                  "away_runs", "home_runs", "away_bb", "home_bb", "away_k", "home_k"]

    written = 0
    failures = []
    with open(OUT_FILE, "w", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()

        for i, g in enumerate(games, 1):
            if i % 50 == 0:
                print(f"  [{i}/{len(games)}] processed ({written} written, {len(failures)} failed)")
            try:
                extra = get_hp_umpire_and_totals(g["game_pk"])
            except Exception as e:
                failures.append((g["game_pk"], str(e)))
                time.sleep(REQUEST_DELAY)
                continue

            row = {**g, **extra}
            writer.writerow(row)
            written += 1
            time.sleep(REQUEST_DELAY)

    print(f"\n[OK] Wrote {written} games to {OUT_FILE}")
    print(f"Failures: {len(failures)}")

    if failures:
        with open(DEBUG_FILE, "w") as fp:
            fp.write(f"{len(failures)} games failed to fetch:\n")
            for pk, err in failures:
                fp.write(f"  gamePk={pk}: {err}\n")
        print(f"Wrote failure details to {DEBUG_FILE}")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_FILE, "w") as fp:
            fp.write("FATAL FAILURE at " + datetime.utcnow().isoformat() + "\n\n")
            fp.write(traceback.format_exc())
        print("Wrote fatal failure details to", DEBUG_FILE)
        raise
