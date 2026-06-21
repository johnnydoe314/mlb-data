#!/usr/bin/env python3
"""
fetch_f5_scores.py — fetch F5 (first 5 inning) scores from the MLB Stats API
and update game_log.csv with away_f5, home_f5, f5_total, f5_result, f5_lean, f5_correct.

Uses:  https://statsapi.mlb.com/api/v1/schedule
       ?sportId=1&date={date}&hydrate=linescore

Runs as part of the daily_data.yml workflow (after update_scores.py).
"""
import csv
import io
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
MLB_API   = "https://statsapi.mlb.com/api/v1"
LOG_FILE  = Path(os.environ.get("LOG_FILE", "data/game_log.csv"))
TIMEOUT   = 20

# Map MLB team abbreviations → our standard abbreviations
MLB_NORM = {
    "AZ":"ARI","KC":"KCR","SD":"SDP","SF":"SFG","TB":"TBR",
    # MLB sometimes uses these for Oakland
    "OAK":"ATH","LAN":"LAD","NYM":"NYM","NYY":"NYY",
}
def norm(t): return MLB_NORM.get(t.strip().upper(), t.strip().upper())


def fetch_linescore(target_date: str) -> dict:
    """
    Fetch linescore data for a given date.
    Returns dict keyed by (away_norm, home_norm) → {away_f5, home_f5, f5_total, f5_result}
    """
    url = (f"{MLB_API}/schedule?sportId=1&date={target_date}"
           f"&hydrate=linescore&gameType=R")
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_f5/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"  [!] MLB API error for {target_date}: {e}", file=sys.stderr)
        return {}

    results = {}
    for day in data.get("dates", []):
        for game in day.get("games", []):
            if game.get("status", {}).get("statusCode") not in ("F","FT","FR"):
                continue  # skip games not final
            teams    = game.get("teams", {})
            at       = norm(teams.get("away", {}).get("team", {}).get("abbreviation", ""))
            ht       = norm(teams.get("home", {}).get("team", {}).get("abbreviation", ""))
            innings  = game.get("linescore", {}).get("innings", [])
            if not at or not ht or not innings:
                continue

            # Sum first 5 innings
            af5 = sum(inn.get("away", {}).get("runs", 0) or 0
                      for inn in innings if inn.get("num", 0) <= 5)
            hf5 = sum(inn.get("home", {}).get("runs", 0) or 0
                      for inn in innings if inn.get("num", 0) <= 5)
            total = af5 + hf5
            result = "AWAY" if af5 > hf5 else ("HOME" if hf5 > af5 else "Tie")

            results[(at, ht)] = {
                "away_f5":   af5,
                "home_f5":   hf5,
                "f5_total":  total,
                "f5_result": result,
            }
    return results


def f5_lean(f5_result, composite):
    """1 if composite direction matches F5 result, 0 if not, '' if tie/no signal."""
    if not f5_result or f5_result == "Tie": return ""
    try:
        adj = float(composite or 0)
    except (ValueError, TypeError):
        return ""
    if abs(adj) < 0.05: return ""
    lean   = "AWAY" if adj > 0 else "HOME"
    actual = "AWAY" if f5_result == "AWAY" else "HOME"
    return 1 if lean == actual else 0


def f5_correct(f5_rec, f5_result, model_dir):
    """1 if F5 rec was set AND model direction matched F5 result."""
    try:
        if int(f5_rec or 0) != 1: return ""
    except (ValueError, TypeError):
        return ""
    if not f5_result or f5_result == "Tie": return ""
    actual = "AWAY" if f5_result == "AWAY" else "HOME"
    return 1 if model_dir == actual else 0


def update_log(target_date: str | None = None) -> int:
    """Update game_log.csv with F5 data for target_date (default: yesterday)."""
    if target_date is None:
        target_date = (datetime.now(ZoneInfo("America/Chicago")).date() - timedelta(days=1)).isoformat()

    print(f"Fetching F5 scores for {target_date}...")
    f5_data = fetch_linescore(target_date)
    if not f5_data:
        print(f"  No F5 data found for {target_date}")
        return 0

    if not LOG_FILE.exists():
        print(f"  [!] {LOG_FILE} not found", file=sys.stderr)
        return 0

    with open(LOG_FILE, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return 0

    fields = list(rows[0].keys())
    # Ensure F5 columns exist
    for col in ("away_f5","home_f5","f5_total","f5_result","f5_lean","f5_correct"):
        if col not in fields:
            idx = fields.index("home_score") + 1 if "home_score" in fields else len(fields)
            fields.insert(idx, col)

    updated = 0
    for row in rows:
        if row.get("game_date","") != target_date: continue
        at = row.get("away_team",""); ht = row.get("home_team","")
        key = (at, ht)
        if key not in f5_data: continue

        f5 = f5_data[key]
        row["away_f5"]    = f5["away_f5"]
        row["home_f5"]    = f5["home_f5"]
        row["f5_total"]   = f5["f5_total"]
        row["f5_result"]  = f5["f5_result"]
        row["f5_lean"]    = f5_lean(f5["f5_result"], row.get("composite",""))
        row["f5_correct"] = f5_correct(row.get("f5_rec",""), f5["f5_result"], row.get("model_dir","NEUT"))
        updated += 1
        print(f"  {at}@{ht}: F5 {f5['away_f5']}-{f5['home_f5']} ({f5['f5_result']})")

    print(f"  Updated {updated} rows")

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return updated


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    updated = update_log(target)
    sys.exit(0 if updated >= 0 else 1)
