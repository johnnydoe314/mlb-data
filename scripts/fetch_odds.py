#!/usr/bin/env python3
"""
fetch_odds.py
=============
Fetches MLB odds from The-Odds-API and saves to data/odds.csv
Free tier: 500 requests/month — run 2-3x daily to stay within limits.

Setup:
  1. Sign up free at https://the-odds-api.com
  2. Add your API key as a GitHub secret named ODDS_API_KEY
  3. Add this step to your workflow (see daily_data.yml)

Output columns:
  game_date, game_time, away_team, home_team,
  away_ml, home_ml, away_rl, home_rl, rl_line,
  total, over_line, under_line, bookmaker, fetched_at
"""

import csv
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR  = Path("data")
OUT_FILE = OUT_DIR / "odds.csv"
TIMEOUT  = 20

# Bookmaker priority order — first one found per game wins
BOOK_PRIORITY = [
    "draftkings", "fanduel", "betmgm",
    "caesars", "pointsbetus", "bovada"
]

FIELDS = [
    "game_date", "game_time", "away_team", "home_team",
    "away_ml", "home_ml",
    "away_rl", "home_rl", "rl_line",
    "total", "over_line", "under_line",
    "bookmaker", "fetched_at"
]

# Team name → abbreviation map
ABBREVS = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL", "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC", "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL", "Detroit Tigers": "DET",
    "Houston Astros": "HOU", "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA", "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SDP", "San Francisco Giants": "SFG",
    "Seattle Mariners": "SEA", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR", "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR", "Washington Nationals": "WSH",
    "Athletics": "ATH",
}


def fetch_odds(api_key: str) -> list[dict]:
    url = (
        "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
        f"?apiKey={api_key}"
        "&regions=us"
        "&markets=h2h,spreads,totals,h2h_h1,spreads_h1"
        "&oddsFormat=american"
        "&dateFormat=iso"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-odds-bot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        # Log remaining quota from response headers
        remaining = r.headers.get("x-requests-remaining", "?")
        used      = r.headers.get("x-requests-used", "?")
        print(f"  API quota: {used} used, {remaining} remaining this month")
        return json.loads(r.read().decode("utf-8"))


def best_book(game: dict) -> dict | None:
    """Find the highest-priority bookmaker available for this game."""
    books = {b["key"]: b for b in game.get("bookmakers", [])}
    for key in BOOK_PRIORITY:
        if key in books:
            return books[key]
    # Fallback: any available book
    return next(iter(books.values()), None) if books else None


def parse_game(game: dict, fetched_at: str) -> dict | None:
    book = best_book(game)
    if not book:
        return None

    # Game time
    try:
        gt = datetime.fromisoformat(game["commence_time"].replace("Z", "+00:00"))
        gt_ct = gt.astimezone(timezone.utc)  # store UTC, display in analysis
        game_date = gt_ct.strftime("%Y-%m-%d")
        game_time = gt_ct.strftime("%H:%M UTC")
    except Exception:
        game_date = game_time = ""

    away = ABBREVS.get(game.get("away_team", ""), game.get("away_team", ""))
    home = ABBREVS.get(game.get("home_team", ""), game.get("home_team", ""))

    # Pull markets
    markets = {m["key"]: m for m in book.get("markets", [])}
    row = {
        "game_date": game_date,
        "game_time": game_time,
        "away_team":  away,
        "home_team":  home,
        "away_ml": "", "home_ml": "",
        "away_rl": "", "home_rl": "", "rl_line": "",
        "total": "", "over_line": "", "under_line": "",
        "bookmaker": book["key"],
        "fetched_at": fetched_at,
    }

    # Moneyline (h2h)
    if "h2h" in markets:
        for outcome in markets["h2h"]["outcomes"]:
            abbrev = ABBREVS.get(outcome["name"], outcome["name"])
            price  = outcome["price"]
            if abbrev == away:
                row["away_ml"] = price
            elif abbrev == home:
                row["home_ml"] = price

    # Spreads (runline)
    if "spreads" in markets:
        for outcome in markets["spreads"]["outcomes"]:
            abbrev = ABBREVS.get(outcome["name"], outcome["name"])
            point  = outcome.get("point", "")
            price  = outcome["price"]
            if abbrev == away:
                row["away_rl"] = price
                row["rl_line"] = point
            elif abbrev == home:
                row["home_rl"] = price

    # Totals
    if "totals" in markets:
        for outcome in markets["totals"]["outcomes"]:
            name  = outcome["name"].lower()
            point = outcome.get("point", "")
            price = outcome["price"]
            if "over" in name:
                row["total"]     = point
                row["over_line"] = price
            elif "under" in name:
                row["under_line"] = price

    return row


def main():
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("[!] ODDS_API_KEY not set — skipping odds fetch.")
        print("    Add your key from the-odds-api.com as a GitHub secret.")
        sys.exit(0)  # exit 0 so workflow doesn't fail without a key

    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 55)
    print(f"  FETCH ODDS — {fetched_at}")
    print("=" * 55)
    print()
    print("  Fetching from The-Odds-API...", end="", flush=True)

    try:
        games_raw = fetch_odds(api_key)
    except urllib.error.HTTPError as e:
        print(f"\n  [!] HTTP {e.code}: {e.read().decode()[:200]}")
        sys.exit(1)
    except Exception as e:
        print(f"\n  [!] Error: {e}")
        sys.exit(1)

    print(f" {len(games_raw)} games returned.")

    rows = []
    for game in games_raw:
        parsed = parse_game(game, fetched_at)
        if parsed:
            rows.append(parsed)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  [✓] Saved: {OUT_FILE}  ({len(rows)} games)")
    print()
    for r in rows:
        print(f"  {r['away_team']:<4} @ {r['home_team']:<4}  "
              f"ML: {r['away_ml']:>5}/{r['home_ml']:<5}  "
              f"RL: {r['away_rl']:>5}/{r['home_rl']:<5} ({r['rl_line']})  "
              f"O/U: {r['total']} ({r['over_line']}/{r['under_line']})")

    print("\n" + "=" * 55)


if __name__ == "__main__":
    main()
