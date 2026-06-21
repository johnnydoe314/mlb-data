#!/usr/bin/env python3
"""
fetch_pitchers.py
=================
Fetches today's probable pitchers from the MLB Stats API.
Saves to data/probable_pitchers.csv — readable by run_analysis.py.
Runs daily via GitHub Actions after 10am CT when SPs are confirmed.
"""

import csv
import json
import sys
import urllib.request
import urllib.error
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

OUT_DIR  = Path("data")
OUT_FILE = OUT_DIR / "probable_pitchers.csv"
TIMEOUT  = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# MLB Stats API uses short abbreviations for some teams.
# Normalize to the standard long form used across all files.
ABBREV_FIX = {
    "TB":  "TBR", "KC":  "KCR", "SD":  "SDP",
    "SF":  "SFG", "AZ":  "ARI", "WAS": "WSH",
}

FIELDS = [
    "game_date","game_pk","game_time","venue",
    "away_team","away_team_name","home_team","home_team_name",
    "away_pitcher_id","away_pitcher","home_pitcher_id","home_pitcher","status"
]

def fetch_schedule(game_date: str) -> list[dict]:
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={game_date}"
        "&hydrate=probablePitcher(note),team,venue"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

def parse_games(data: dict) -> list[dict]:
    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_p = away.get("probablePitcher", {})
            home_p = home.get("probablePitcher", {})

            # Format pitcher name as "Last, First" to match stats.csv
            def fmt(pitcher: dict) -> tuple[str, str]:
                if not pitcher:
                    return "", "TBD"
                pid  = str(pitcher.get("id", ""))
                full = pitcher.get("fullName", "")
                parts = full.strip().split(" ", 1)
                if len(parts) == 2:
                    name = f"{parts[1]}, {parts[0]}"
                else:
                    name = full
                return pid, name

            away_pid, away_name = fmt(away_p)
            home_pid, home_name = fmt(home_p)

            # Game time in ET → local display
            game_time_raw = g.get("gameDate", "")
            try:
                gt = datetime.strptime(game_time_raw, "%Y-%m-%dT%H:%MZ")
                game_time = gt.strftime("%-I:%M PM ET")
            except Exception:
                game_time = game_time_raw

            games.append({
                "game_date":      date_entry.get("date", ""),
                "game_pk":        str(g.get("gamePk", "")),
                "game_time":      game_time,
                "venue":          g.get("venue", {}).get("name", ""),
                "away_team":      ABBREV_FIX.get(away["team"].get("abbreviation", ""), away["team"].get("abbreviation", "")),
                "away_team_name": away["team"].get("name", ""),
                "home_team":      ABBREV_FIX.get(home["team"].get("abbreviation", ""), home["team"].get("abbreviation", "")),
                "home_team_name": home["team"].get("name", ""),
                "away_pitcher_id": away_pid,
                "away_pitcher":    away_name,
                "home_pitcher_id": home_pid,
                "home_pitcher":    home_name,
                "status":         g.get("status", {}).get("detailedState", ""),
            })
    return games

def save(games: list[dict]):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(games)

def main():
    # Anchor to US/Central explicitly — date.today() uses the runner's
    # system clock (UTC on GitHub Actions), and UTC midnight falls at
    # 7pm CDT, which silently rolled "today" over to tomorrow for any
    # run in the 7pm-midnight Central window. This was confirmed live:
    # at 8:06pm CT on 6/20, date.today() was already returning 6/21.
    today = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")
    print("=" * 55)
    print(f"  FETCH PROBABLE PITCHERS — {today}")
    print("=" * 55)

    print(f"\n  Fetching from MLB Stats API...", end="", flush=True)
    try:
        data  = fetch_schedule(today)
        games = parse_games(data)
    except urllib.error.HTTPError as e:
        print(f"\n  [!] HTTP {e.code}: {e.reason}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [!] Error: {e}")
        sys.exit(1)

    print(f" {len(games)} games found.")
    save(games)
    print(f"  [✓] Saved: {OUT_FILE}")

    tbd = sum(1 for g in games if g["away_pitcher"] == "TBD"
                                or g["home_pitcher"] == "TBD")
    confirmed = len(games) - (tbd // 2)
    print(f"  Games: {len(games)} total  |  TBD pitchers: {tbd}")
    print()
    for g in games:
        print(f"  {g['away_team']:<4} @ {g['home_team']:<4}  "
              f"{g['away_pitcher']:<22} vs {g['home_pitcher']}")
    print("\n" + "=" * 55)

if __name__ == "__main__":
    main()
