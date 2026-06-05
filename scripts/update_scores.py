#!/usr/bin/env python3
"""
update_scores.py
================
Fill in actual scores + model_correct for yesterday's games in game_log.csv.
Run each morning after overnight game completion.

Usage:
    python scripts/update_scores.py --date 2026-06-03

Fetches final scores from MLB Stats API and matches to game_log.csv rows.
"""

import csv, json, sys, urllib.request
from datetime import date, timedelta
from pathlib import Path

LOG_FILE = Path("data/game_log.csv")
TIMEOUT  = 20

TEAM_NAME_MAP = {
    "Athletics": "ATH", "Oakland Athletics": "ATH",
    "Diamondbacks": "ARI", "Arizona Diamondbacks": "ARI",
    "Braves": "ATL", "Atlanta Braves": "ATL",
    "Orioles": "BAL", "Baltimore Orioles": "BAL",
    "Red Sox": "BOS", "Boston Red Sox": "BOS",
    "Cubs": "CHC", "Chicago Cubs": "CHC",
    "White Sox": "CWS", "Chicago White Sox": "CWS",
    "Reds": "CIN", "Cincinnati Reds": "CIN",
    "Guardians": "CLE", "Cleveland Guardians": "CLE",
    "Rockies": "COL", "Colorado Rockies": "COL",
    "Tigers": "DET", "Detroit Tigers": "DET",
    "Astros": "HOU", "Houston Astros": "HOU",
    "Royals": "KCR", "Kansas City Royals": "KCR",
    "Angels": "LAA", "Los Angeles Angels": "LAA",
    "Dodgers": "LAD", "Los Angeles Dodgers": "LAD",
    "Marlins": "MIA", "Miami Marlins": "MIA",
    "Brewers": "MIL", "Milwaukee Brewers": "MIL",
    "Twins": "MIN", "Minnesota Twins": "MIN",
    "Mets": "NYM", "New York Mets": "NYM",
    "Yankees": "NYY", "New York Yankees": "NYY",
    "Phillies": "PHI", "Philadelphia Phillies": "PHI",
    "Pirates": "PIT", "Pittsburgh Pirates": "PIT",
    "Padres": "SDP", "San Diego Padres": "SDP",
    "Mariners": "SEA", "Seattle Mariners": "SEA",
    "Giants": "SFG", "San Francisco Giants": "SFG",
    "Cardinals": "STL", "St. Louis Cardinals": "STL",
    "Rays": "TBR", "Tampa Bay Rays": "TBR",
    "Rangers": "TEX", "Texas Rangers": "TEX",
    "Blue Jays": "TOR", "Toronto Blue Jays": "TOR",
    "Nationals": "WSH", "Washington Nationals": "WSH",
}


def fetch_scores(game_date: str) -> dict:
    """Fetch final scores from MLB Stats API."""
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&date={game_date}&hydrate=linescore")
    req = urllib.request.Request(url, headers={"User-Agent": "update_scores/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read().decode())

    scores = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            status = game.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue
            teams = game.get("teams", {})
            away  = TEAM_NAME_MAP.get(
                teams.get("away",{}).get("team",{}).get("name",""), "")
            home  = TEAM_NAME_MAP.get(
                teams.get("home",{}).get("team",{}).get("name",""), "")
            a_score = teams.get("away",{}).get("score", "")
            h_score = teams.get("home",{}).get("score", "")
            if away and home:
                scores[(away, home)] = (a_score, h_score)
    return scores


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date",
                   default=(date.today() - timedelta(days=1)).isoformat())
    args = p.parse_args()
    game_date = args.date

    if not LOG_FILE.exists():
        print("No game_log.csv found.")
        sys.exit(0)

    print(f"Fetching scores for {game_date}...")
    scores = fetch_scores(game_date)
    print(f"  Found {len(scores)} final games from API")

    rows = []
    updated = 0
    with open(LOG_FILE, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    fields = list(rows[0].keys()) if rows else []

    for row in rows:
        if row['game_date'] != game_date:
            continue
        at = row['away_team']; ht = row['home_team']
        result = scores.get((at, ht))
        if not result:
            continue
        a_score, h_score = result
        row['away_score'] = a_score
        row['home_score'] = h_score

        try:
            a, h = int(a_score), int(h_score)
            if a == h:
                row['model'] = ''
                row['lean']  = ''
            else:
                actual = 'AWAY' if a > h else 'HOME'
                model  = row.get('model_dir', 'NEUT')
                qual   = int(row.get('qualified', 0) or 0)

                # model_correct — only for qualified plays (|comp|>=5, aligned, not MISS)
                if qual and model != 'NEUT':
                    row['model'] = 1 if model == actual else 0
                else:
                    row['model'] = ''

                # lean_correct — composite lean direction for all games
                try:
                    comp = float(row.get('composite', 0) or 0)
                    if abs(comp) >= 0.05:   # ignore near-zero composites
                        lean = 'AWAY' if comp > 0 else 'HOME'
                        row['lean'] = 1 if lean == actual else 0
                    else:
                        row['lean'] = ''
                except (ValueError, TypeError):
                    row['lean'] = ''

        except (ValueError, TypeError):
            row['model'] = ''
            row['lean']  = ''

        updated += 1
        mc  = row['model']
        lc  = row['lean']
        mc_str = '✅' if mc == 1 else ('❌' if mc == 0 else '~')
        lc_str = '✅' if lc == 1 else ('❌' if lc == 0 else '~')
        print(f"  {at}@{ht}: {a_score}-{h_score} | model={model} qual={qual} → MC:{mc_str} LC:{lc_str}")

    with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [✓] {updated} rows updated in {LOG_FILE}")


if __name__ == "__main__":
    main()
