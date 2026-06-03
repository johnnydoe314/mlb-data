#!/usr/bin/env python3
"""
fetch_all_odds.py
=================
Single unified odds fetch — gets full game AND F5 lines in one efficient pass.

Strategy (stays within 500 calls/month free tier):
  Step 1: GET /v4/sports/baseball_mlb/events → event IDs  (1 call)
  Step 2: For each game:
          GET /v4/sports/baseball_mlb/events/{id}/odds
              ?markets=h2h,spreads,totals,h2h_h1,totals_h1
          → full game ML + RL + O/U + F5 ML + F5 O/U in one call (15 calls)

  Total: 16 calls/day × 30 days = 480/month  ✅ (free tier is 500)

Replaces: fetch_odds.py (hourly bulk) + fetch_f5_odds.py (separate F5)
Schedule: Once daily at 10am CT in daily_data.yml

Output:
  data/odds.csv    — full game: away_ml, home_ml, away_rl, home_rl, rl_line,
                                total, over_line, under_line
  data/f5_odds.csv — F5:        away_f5_ml, home_f5_ml,
                                f5_total, f5_over_line, f5_under_line
"""

import csv, json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR      = Path("data")
ODDS_FILE    = OUT_DIR / "odds.csv"
F5_FILE      = OUT_DIR / "f5_odds.csv"
TIMEOUT      = 20

BOOK_PRIORITY = [
    "draftkings", "fanduel", "betmgm",
    "caesars", "pointsbetus", "bovada"
]

ABBREVS = {
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

ODDS_FIELDS = [
    "game_date","game_time","away_team","home_team",
    "away_ml","home_ml","away_rl","home_rl","rl_line",
    "total","over_line","under_line","bookmaker","fetched_at"
]

F5_FIELDS = [
    "game_date","game_time","away_team","home_team",
    "away_f5_ml","home_f5_ml",
    "f5_total","f5_over_line","f5_under_line",
    "bookmaker","event_id","fetched_at"
]


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-odds-bot/2.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        remaining = r.headers.get("x-requests-remaining", "?")
        used      = r.headers.get("x-requests-used", "?")
        return json.loads(r.read().decode()), remaining, used


def best_book(bookmakers):
    books = {b["key"]: b for b in bookmakers}
    for key in BOOK_PRIORITY:
        if key in books:
            return books[key]
    return next(iter(books.values()), None) if books else None


def parse_event(event: dict, odds_data: dict, fetched_at: str):
    """Parse one event's odds into two flat rows (full game + F5)."""
    away = ABBREVS.get(event.get("away_team",""), event.get("away_team",""))
    home = ABBREVS.get(event.get("home_team",""), event.get("home_team",""))

    try:
        gt = datetime.fromisoformat(
            event["commence_time"].replace("Z","+00:00")
        ).astimezone(timezone.utc)
        game_date = gt.strftime("%Y-%m-%d")
        game_time = gt.strftime("%H:%M UTC")
    except Exception:
        game_date = game_time = ""

    book = best_book(odds_data.get("bookmakers", []))
    if not book:
        return None, None

    markets = {m["key"]: m for m in book.get("markets", [])}
    bk = book["key"]
    eid = event.get("id","")

    # ── Full game row ─────────────────────────────────────────────────
    full = {
        "game_date": game_date, "game_time": game_time,
        "away_team": away, "home_team": home,
        "away_ml":"","home_ml":"","away_rl":"","home_rl":"","rl_line":"",
        "total":"","over_line":"","under_line":"",
        "bookmaker": bk, "fetched_at": fetched_at,
    }
    if "h2h" in markets:
        for o in markets["h2h"]["outcomes"]:
            a = ABBREVS.get(o["name"], o["name"])
            if a == away: full["away_ml"] = o["price"]
            elif a == home: full["home_ml"] = o["price"]
    if "spreads" in markets:
        for o in markets["spreads"]["outcomes"]:
            a = ABBREVS.get(o["name"], o["name"])
            if a == away:
                full["away_rl"] = o["price"]
                full["rl_line"] = o.get("point","")
            elif a == home:
                full["home_rl"] = o["price"]
    if "totals" in markets:
        for o in markets["totals"]["outcomes"]:
            n = o["name"].lower()
            if "over" in n:
                full["total"] = o.get("point","")
                full["over_line"] = o["price"]
            elif "under" in n:
                full["under_line"] = o["price"]

    # ── F5 row ────────────────────────────────────────────────────────
    f5 = {
        "game_date": game_date, "game_time": game_time,
        "away_team": away, "home_team": home,
        "away_f5_ml":"","home_f5_ml":"",
        "f5_total":"","f5_over_line":"","f5_under_line":"",
        "bookmaker": bk, "event_id": eid, "fetched_at": fetched_at,
    }
    if "h2h_h1" in markets:
        for o in markets["h2h_h1"]["outcomes"]:
            a = ABBREVS.get(o["name"], o["name"])
            if a == away: f5["away_f5_ml"] = o["price"]
            elif a == home: f5["home_f5_ml"] = o["price"]
    if "totals_h1" in markets:
        for o in markets["totals_h1"]["outcomes"]:
            n = o["name"].lower()
            if "over" in n:
                f5["f5_total"] = o.get("point","")
                f5["f5_over_line"] = o["price"]
            elif "under" in n:
                f5["f5_under_line"] = o["price"]

    # Only include F5 if we got at least moneyline
    f5_valid = bool(f5["away_f5_ml"] or f5["home_f5_ml"])
    return full, (f5 if f5_valid else None)


def main():
    api_key = os.environ.get("ODDS_API_KEY","")
    if not api_key:
        print("[!] ODDS_API_KEY not set — skipping odds fetch.")
        sys.exit(0)

    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("="*58)
    print(f"  FETCH ALL ODDS — {fetched_at}")
    print(f"  Full game + F5 in one unified pass (16 calls/day)")
    print("="*58)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Event IDs
    print(f"\n  [1/2] Fetching today's events...", end="", flush=True)
    try:
        events, remaining, used = get(
            f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events"
            f"?apiKey={api_key}&dateFormat=iso"
        )
    except urllib.error.HTTPError as e:
        print(f"\n  [!] HTTP {e.code}: {e.read().decode()[:150]}")
        sys.exit(1)

    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_events = [e for e in events if e.get("commence_time","").startswith(today)]
    print(f" {len(today_events)} games today | quota: {used} used, {remaining} left")

    # Step 2: Per-game odds (all markets)
    print(f"  [2/2] Fetching full game + F5 odds per game...")
    odds_rows, f5_rows = [], []

    for i, event in enumerate(today_events, 1):
        away = ABBREVS.get(event.get("away_team",""), event.get("away_team",""))
        home = ABBREVS.get(event.get("home_team",""), event.get("home_team",""))
        eid  = event.get("id","")

        url = (
            f"https://api.the-odds-api.com/v4/sports/baseball_mlb"
            f"/events/{eid}/odds"
            f"?apiKey={api_key}"
            f"&regions=us"
            f"&markets=h2h,spreads,totals,h2h_h1,totals_h1"
            f"&oddsFormat=american"
            f"&dateFormat=iso"
        )

        try:
            odds_data, remaining, _ = get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"    [{i:2}] {away}@{home} — no odds available")
                continue
            raise
        except Exception as e:
            print(f"    [{i:2}] {away}@{home} — error: {e}")
            continue

        full_row, f5_row = parse_event(event, odds_data, fetched_at)

        if full_row and (full_row["away_ml"] or full_row["home_ml"]):
            odds_rows.append(full_row)
        if f5_row:
            f5_rows.append(f5_row)

        # Display
        ml_str  = f"ML {full_row['away_ml']}/{full_row['home_ml']}" if full_row else "—"
        f5_str  = f"F5 {f5_row['away_f5_ml']}/{f5_row['home_f5_ml']}" if f5_row else "no F5"
        print(f"    [{i:2}] {away}@{home:<6}  {ml_str:<18} {f5_str}  (quota: {remaining} left)")
        time.sleep(0.25)

    # Save
    with open(ODDS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ODDS_FIELDS, quoting=csv.QUOTE_ALL)
        w.writeheader(); w.writerows(odds_rows)

    with open(F5_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=F5_FIELDS, quoting=csv.QUOTE_ALL)
        w.writeheader(); w.writerows(f5_rows)

    print(f"\n  [✓] odds.csv    — {len(odds_rows)} games")
    print(f"  [✓] f5_odds.csv — {len(f5_rows)} games with F5 lines")
    print(f"\n  Monthly estimate: 16 calls/day × 30 days = 480 (free tier: 500) ✅")
    print("="*58)


if __name__ == "__main__":
    main()
