#!/usr/bin/env python3
"""
fetch_bullpen_usage.py
======================
Tracks reliever availability by monitoring who pitched in the last 3 days.

Logic:
  1. Fetch boxscores for yesterday (and prior 2 days from rolling history)
  2. For each reliever: calculate rest days + consecutive days used
  3. Apply availability weights:
       Pitched yesterday         → 0.50 base
       1 day rest                → 0.75 base
       2+ days rest              → 1.00 base
       ×0.70 if 2 consecutive    (setup/closer tier)
       ×0.40 if 3 consecutive    (setup/closer tier)
       ×0.85 if high pitch count (>25 pitches yesterday)

  4. Team fatigue score = weighted average of top 4 arms by role priority
  5. Bullpen gap gets multiplied by team fatigue score in composite model

Output:
  data/bullpen_fatigue.csv     — per-team summary (team, fatigue_score, arms_tired)
  data/bullpen_usage.json      — rolling 3-day pitcher appearance history

Run daily via GitHub Actions AFTER fetch_pitchers.py (needs rosters).
"""

import csv
import json
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

OUT_DIR       = Path("data")
FATIGUE_FILE  = OUT_DIR / "bullpen_fatigue.csv"
HISTORY_FILE  = OUT_DIR / "bullpen_usage.json"
YEAR          = 2026
TIMEOUT       = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

ABBREV_FIX = {
    "TB":  "TBR", "KC":  "KCR", "SD":  "SDP",
    "SF":  "SFG", "AZ":  "ARI", "WAS": "WSH",
}

FATIGUE_FIELDS = [
    "team", "fatigue_score", "arms_tired", "arms_rested",
    "high_lev_available", "top_arms_detail", "fetched_at"
]

# ── Availability weight table ─────────────────────────────────────────────────

def availability_weight(days_rest: int, consecutive: int, high_pitch: bool) -> float:
    """
    Calculate a 0–1 availability score for a reliever.
    1.0 = fully fresh, 0.0 = unavailable.
    """
    # Base weight from rest days
    if days_rest == 0:
        base = 0.50
    elif days_rest == 1:
        base = 0.75
    else:
        base = 1.00

    # Consecutive-day penalty (for high-leverage arms)
    if consecutive >= 3:
        consec_mult = 0.40
    elif consecutive == 2:
        consec_mult = 0.70
    else:
        consec_mult = 1.00

    # High pitch count penalty
    pitch_mult = 0.85 if high_pitch else 1.00

    return round(base * consec_mult * pitch_mult, 3)


# ── MLB Stats API helpers ─────────────────────────────────────────────────────

def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_boxscores(game_date: str) -> list[dict]:
    """
    Returns list of {gamePk, away_team, home_team, pitchers: [{id, team, pitches, ip}]}
    """
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={game_date}&hydrate=boxscore"
    )
    try:
        data = get(url)
    except Exception as e:
        print(f"    Schedule fetch error for {game_date}: {e}")
        return []

    games = []
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            pk     = game.get("gamePk")
            status = game.get("status", {}).get("abstractGameState", "")
            if status not in ("Final", "Live"):
                continue

            box = game.get("boxscore") or {}
            if not box:
                # Fetch separately if not hydrated
                try:
                    box_data = get(
                        f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"
                    )
                    box = box_data
                except Exception:
                    continue

            pitchers_in_game = []
            for side in ("away", "home"):
                team_data = box.get("teams", {}).get(side, {})
                team_info = team_data.get("team", {})
                abbrev    = ABBREV_FIX.get(
                    team_info.get("abbreviation", ""), 
                    team_info.get("abbreviation", "")
                )
                players   = team_data.get("players", {})

                for player_key, player_data in players.items():
                    pos  = player_data.get("position", {}).get("abbreviation", "")
                    if pos != "P":
                        continue
                    stats  = player_data.get("stats", {})
                    pstat  = stats.get("pitching", {})
                    if not pstat:
                        continue
                    pid    = player_data.get("person", {}).get("id")
                    name   = player_data.get("person", {}).get("fullName", "")
                    gs     = int(pstat.get("gamesStarted", 0) or 0)
                    pitches= int(pstat.get("pitchesThrown", 0) or 0)
                    ip_raw = pstat.get("inningsPitched", "0.0")
                    try:
                        ip = float(ip_raw)
                    except (ValueError, TypeError):
                        ip = 0.0

                    if pitches == 0:   # didn't actually pitch
                        continue
                    if gs > 0 and ip > 2.0:  # skip starters (but keep openers)
                        continue

                    pitchers_in_game.append({
                        "id":     pid,
                        "name":   name,
                        "team":   abbrev,
                        "pitches": pitches,
                        "ip":     ip,
                        "date":   game_date,
                    })

            if pitchers_in_game:
                games.append({
                    "gamePk": pk,
                    "date":   game_date,
                    "pitchers": pitchers_in_game,
                })

    return games


# ── Rolling history ───────────────────────────────────────────────────────────

def load_history() -> dict:
    """Load rolling 3-day pitcher appearance history."""
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return {}   # {date_str: [{id, name, team, pitches, ip}]}


def update_history(history: dict, games: list[dict], game_date: str) -> dict:
    """Update history with today's appearances, pruning entries older than 4 days."""
    appearances = []
    for game in games:
        appearances.extend(game["pitchers"])
    history[game_date] = appearances

    # Keep only last 4 days
    cutoff = (date.fromisoformat(game_date) - timedelta(days=3)).isoformat()
    history = {d: v for d, v in history.items() if d >= cutoff}
    return history


def save_history(history: dict):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


# ── Fatigue calculation ───────────────────────────────────────────────────────

def build_fatigue(history: dict, today: str) -> list[dict]:
    """
    For each reliever seen in the last 3 days, calculate availability.
    Aggregate by team to produce team-level fatigue scores.
    """
    today_dt  = date.fromisoformat(today)
    dates_desc = sorted(history.keys(), reverse=True)  # most recent first

    # Build per-pitcher appearance record
    # pitcher_appearances[pid] = {name, team, appearances: [date, ...], pitches_by_date: {date: int}}
    pitcher_record: dict[int, dict] = {}

    for day, appearances in history.items():
        for arm in appearances:
            pid = arm.get("id")
            if not pid:
                continue
            if pid not in pitcher_record:
                pitcher_record[pid] = {
                    "name":    arm["name"],
                    "team":    arm["team"],
                    "dates":   [],
                    "pitches": {},
                }
            rec = pitcher_record[pid]
            if day not in rec["dates"]:
                rec["dates"].append(day)
            rec["pitches"][day] = arm.get("pitches", 0)

    # Calculate availability per pitcher
    team_arms: dict[str, list] = defaultdict(list)

    for pid, rec in pitcher_record.items():
        rec_dates = sorted(rec["dates"], reverse=True)  # most recent first
        latest    = rec_dates[0]
        latest_dt = date.fromisoformat(latest)
        days_rest = (today_dt - latest_dt).days - 1   # -1 because "yesterday" = 0 rest

        # Consecutive days used (going back from latest)
        consecutive = 1
        for i in range(1, len(rec_dates)):
            prev   = date.fromisoformat(rec_dates[i])
            expect = date.fromisoformat(rec_dates[i - 1]) - timedelta(days=1)
            if prev == expect:
                consecutive += 1
            else:
                break

        yesterday     = (today_dt - timedelta(days=1)).isoformat()
        pitches_yest  = rec["pitches"].get(yesterday, 0)
        high_pitch    = pitches_yest > 25

        avail = availability_weight(
            days_rest   = max(days_rest, 0),
            consecutive = consecutive if days_rest == 0 else 0,
            high_pitch  = high_pitch,
        )

        team_arms[rec["team"]].append({
            "pid":         pid,
            "name":        rec["name"],
            "days_rest":   max(days_rest, 0),
            "consecutive": consecutive if days_rest == 0 else 0,
            "pitches_yest": pitches_yest,
            "availability": avail,
        })

    # Team-level summary: use top 4 arms by lowest availability (most tired = most important to flag)
    now_str  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    rows     = []

    for team, arms in sorted(team_arms.items()):
        # Sort by availability ascending (most fatigued first)
        arms_sorted = sorted(arms, key=lambda x: x["availability"])
        top4        = arms_sorted[:4]

        fatigue_score = round(
            sum(a["availability"] for a in top4) / max(len(top4), 1), 3
        )
        arms_tired  = sum(1 for a in arms if a["availability"] < 0.75)
        arms_rested = sum(1 for a in arms if a["availability"] >= 1.00)
        high_lev_ok = sum(1 for a in top4 if a["availability"] >= 0.75) >= 2

        # Human-readable top arms detail
        detail_parts = []
        for a in arms_sorted[:3]:
            rest_str  = f"{a['days_rest']}d rest" if a['days_rest'] > 0 else "pitched yest"
            detail_parts.append(f"{a['name'].split(',')[0].strip()} ({rest_str}, avail:{a['availability']})")
        detail = " | ".join(detail_parts)

        rows.append({
            "team":             team,
            "fatigue_score":    fatigue_score,
            "arms_tired":       arms_tired,
            "arms_rested":      arms_rested,
            "high_lev_available": int(high_lev_ok),
            "top_arms_detail":  detail,
            "fetched_at":       now_str,
        })

    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today     = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ts        = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 58)
    print(f"  FETCH BULLPEN USAGE — {ts}")
    print(f"  Tracking reliever appearances through {yesterday}")
    print("=" * 58)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Load rolling history
    history = load_history()
    print(f"\n  [1/3] Loaded history: {len(history)} days on record")

    # 2. Fetch yesterday's boxscores (and 2 prior days if missing)
    print(f"  [2/3] Fetching boxscores...")
    dates_to_fetch = []
    for i in range(1, 4):  # yesterday + 2 days before
        d = (date.today() - timedelta(days=i)).isoformat()
        if d not in history:
            dates_to_fetch.append(d)

    if dates_to_fetch:
        for fetch_date in dates_to_fetch:
            print(f"    Fetching {fetch_date}...", end="", flush=True)
            games = fetch_boxscores(fetch_date)
            appearances = sum(len(g["pitchers"]) for g in games)
            print(f" {len(games)} games, {appearances} reliever appearances")
            history = update_history(history, games, fetch_date)
            time.sleep(0.3)
    else:
        print(f"    History is current — no new fetches needed")

    save_history(history)
    print(f"  [✓] History saved ({len(history)} days)")

    # 3. Build fatigue scores
    print(f"\n  [3/3] Computing team fatigue scores...")
    rows = build_fatigue(history, today)

    # 4. Save
    with open(FATIGUE_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FATIGUE_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [✓] {FATIGUE_FILE} — {len(rows)} teams")
    print()
    print(f"  {'Team':<6} {'Fatigue':>8} {'Tired':>6} {'Rested':>7} {'HighLev':>8}")
    print("  " + "─" * 40)
    for r in sorted(rows, key=lambda x: x["fatigue_score"]):
        flag = " ⚠ DEPLETED" if r["fatigue_score"] < 0.70 else (
               " ↑ FRESH"   if r["fatigue_score"] > 0.92 else "")
        print(f"  {r['team']:<6} {r['fatigue_score']:>8.3f} {r['arms_tired']:>6} "
              f"{r['arms_rested']:>7} {r['high_lev_available']:>8}{flag}")

    print()
    # Show detail for teams in tonight's games
    tonight_teams = {
        "SDP","PHI","CLE","NYY","DET","TBR","BAL","BOS",
        "MIA","WSH","KCR","CIN","TOR","ATL","CWS","MIN",
        "SFG","MIL","TEX","STL","ATH","CHC","PIT","HOU",
        "COL","LAA","LAD","ARI","NYM","SEA"
    }
    print("  Top arm detail for key teams tonight:")
    for r in sorted(rows, key=lambda x: x["fatigue_score"]):
        if r["team"] in tonight_teams and r["fatigue_score"] < 0.85:
            print(f"  {r['team']}: {r['top_arms_detail']}")
    print()
    print("=" * 58)


if __name__ == "__main__":
    main()
