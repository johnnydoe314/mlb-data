#!/usr/bin/env python3
"""
fetch_f5_odds.py
================
Fetches First 5 Innings (F5) odds from The-Odds-API.

F5 lines are "period markets" — they require a two-step approach:
  Step 1: GET /v4/sports/baseball_mlb/events → get event IDs for today
  Step 2: GET /v4/sports/baseball_mlb/events/{id}/odds?markets=h2h_h1,totals_h1
          → get F5 moneyline + F5 over/under per game

Market keys:
  h2h_h1     = F5 Moneyline (1st half = first 5 innings in baseball)
  totals_h1  = F5 Over/Under

Cost: ~16 API calls per run (1 events list + 15 per-game calls)
Recommended: Run once daily at 10am CT
Monthly cost: ~480 calls/month → requires paid plan (free tier is 500 total)

NOTE: The-Odds-API paid plan is $10/month for 30,000 calls — covers all
      our usage: 300 (hourly odds) + 480 (F5 daily) = 780/month
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR  = Path("data")
OUT_FILE = OUT_DIR / "f5_odds.csv"
TIMEOUT  = 20

BOOK_PRIORITY = [
    "draftkings", "fanduel", "betmgm",
    "caesars", "pointsbetus", "bovada"
]

ABBREV_FIX = {
    "Arizona Diamondbacks": "ARI",  "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",     "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",          "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",       "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",      "Detroit Tigers": "DET",
    "Houston Astros": "HOU",        "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA",    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",         "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",       "New York Mets": "NYM",
    "New York Yankees": "NYY",      "Oakland Athletics": "ATH",
    "Athletics": "ATH",             "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",    "San Diego Padres": "SDP",
    "San Francisco Giants": "SFG",  "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",   "Tampa Bay Rays": "TBR",
    "Texas Rangers": "TEX",         "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

FIELDS = [
    "game_date", "game_time", "away_team", "home_team",
    "away_f5_ml", "home_f5_ml",
    "f5_total", "f5_over_line", "f5_under_line",
    "bookmaker", "event_id", "fetched_at"
]


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-f5-bot/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        remaining = r.headers.get("x-requests-remaining", "?")
        return json.loads(r.read().decode()), remaining


def fetch_events(api_key: str) -> list[dict]:
    """Step 1: Get all MLB events for today with their event IDs."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
        f"?apiKey={api_key}&dateFormat=iso"
    )
    data, remaining = get(url)
    print(f"  Events fetched: {len(data)} | Quota remaining: {remaining}")
    return data


def fetch_event_f5(api_key: str, event_id: str) -> dict | None:
    """Step 2: Fetch F5 odds for a single event."""
    url = (
        f"https://api.the-odds-api.com/v4/sports/baseball_mlb"
        f"/events/{event_id}/odds"
        f"?apiKey={api_key}"
        f"&regions=us"
        f"&markets=h2h_h1,totals_h1"
        f"&oddsFormat=american"
        f"&dateFormat=iso"
    )
    try:
        data, _ = get(url)
        return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None   # event not found / no F5 lines
        raise
    except Exception as e:
        print(f"    Error fetching event {event_id}: {e}")
        return None


def best_book(bookmakers: list[dict]) -> dict | None:
    books = {b["key"]: b for b in bookmakers}
    for key in BOOK_PRIORITY:
        if key in books:
            return books[key]
    return next(iter(books.values()), None) if books else None


def parse_event(event: dict, odds_data: dict, fetched_at: str) -> dict | None:
    """Parse F5 odds from event + odds response into a flat row."""
    away  = ABBREV_FIX.get(event.get("away_team", ""), event.get("away_team", ""))
    home  = ABBREV_FIX.get(event.get("home_team", ""), event.get("home_team", ""))

    try:
        gt = datetime.fromisoformat(
            event["commence_time"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        game_date = gt.strftime("%Y-%m-%d")
        game_time = gt.strftime("%H:%M UTC")
    except Exception:
        game_date = game_time = ""

    bookmakers = odds_data.get("bookmakers", [])
    book = best_book(bookmakers)
    if not book:
        return None

    markets = {m["key"]: m for m in book.get("markets", [])}
    row = {
        "game_date":    game_date,
        "game_time":    game_time,
        "away_team":    away,
        "home_team":    home,
        "away_f5_ml":   "",
        "home_f5_ml":   "",
        "f5_total":     "",
        "f5_over_line": "",
        "f5_under_line": "",
        "bookmaker":    book["key"],
        "event_id":     event.get("id", ""),
        "fetched_at":   fetched_at,
    }

    # F5 Moneyline
    if "h2h_h1" in markets:
        for outcome in markets["h2h_h1"]["outcomes"]:
            abbrev = ABBREV_FIX.get(outcome["name"], outcome["name"])
            price  = outcome["price"]
            if abbrev == away:
                row["away_f5_ml"] = price
            elif abbrev == home:
                row["home_f5_ml"] = price

    # F5 Total
    if "totals_h1" in markets:
        for outcome in markets["totals_h1"]["outcomes"]:
            name  = outcome["name"].lower()
            point = outcome.get("point", "")
            price = outcome["price"]
            if "over" in name:
                row["f5_total"]     = point
                row["f5_over_line"] = price
            elif "under" in name:
                row["f5_under_line"] = price

    # Only return if we got at least the moneyline
    if row["away_f5_ml"] or row["home_f5_ml"]:
        return row
    return None


def main():
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("[!] ODDS_API_KEY not set — skipping F5 fetch.")
        sys.exit(0)

    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 55)
    print(f"  FETCH F5 ODDS — {fetched_at}")
    print("=" * 55)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Get events
    print("\n  [1/2] Fetching today's events...")
    try:
        events = fetch_events(api_key)
    except urllib.error.HTTPError as e:
        print(f"  [!] HTTP {e.code}: {e.read().decode()[:200]}")
        sys.exit(1)

    # Filter to today only
    today = datetime.utcnow().strftime("%Y-%m-%d")
    todays_events = [
        e for e in events
        if e.get("commence_time", "").startswith(today)
    ]
    print(f"  Today's games: {len(todays_events)} of {len(events)} total")

    # Step 2: Fetch F5 per event
    print(f"\n  [2/2] Fetching F5 odds per game...")
    rows = []

    for i, event in enumerate(todays_events, 1):
        away  = ABBREV_FIX.get(event.get("away_team",""), event.get("away_team",""))
        home  = ABBREV_FIX.get(event.get("home_team",""), event.get("home_team",""))
        eid   = event.get("id","")

        odds_data = fetch_event_f5(api_key, eid)
        if not odds_data:
            print(f"    [{i:2}] {away}@{home} — no F5 lines available")
            continue

        parsed = parse_event(event, odds_data, fetched_at)
        if parsed:
            rows.append(parsed)
            ml_str = f"{parsed['away_f5_ml']}/{parsed['home_f5_ml']}"
            ou_str = f"F5 O/U {parsed['f5_total']}" if parsed['f5_total'] else ""
            print(f"    [{i:2}] {away}@{home}  F5 ML: {ml_str}  {ou_str}")
        else:
            print(f"    [{i:2}] {away}@{home} — F5 lines incomplete")

        time.sleep(0.3)  # be respectful to the API

    # Save
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [✓] {OUT_FILE} — {len(rows)} games with F5 lines")
    print("=" * 55)


if __name__ == "__main__":
    main()
