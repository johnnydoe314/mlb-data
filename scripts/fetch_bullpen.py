#!/usr/bin/env python3
"""
fetch_bullpen.py
================
Builds team-level bullpen xwOBA gap data by combining:
  1. MLB Stats API  → player IDs, team affiliations, games started vs total
  2. Baseball Savant stats.csv → xwOBA, wOBA, hard_hit% per pitcher

Logic:
  - Relievers = pitchers where gamesStarted / gamesPitched < 0.33
  - Group qualifying relievers by team
  - Calculate PA-weighted average wOBA gap (wOBA - xwOBA)
  - Positive gap = bullpen ERA running better than deserved (regression risk)
  - Negative gap = bullpen ERA running worse than true talent (improving)

Output: data/bullpen.csv
  Team, bullpen_pa, bullpen_woba, bullpen_xwoba, bullpen_gap,
  bullpen_hard_hit, bullpen_k_pct, pitchers_counted, fetched_at
"""

import csv
import io
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR  = Path("data")
OUT_FILE = OUT_DIR / "bullpen.csv"
STATS_FILE = Path("data/stats.csv")
YEAR     = 2026
TIMEOUT  = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Reliever threshold: if fewer than 1/3 of appearances are starts → reliever
RP_THRESHOLD = 0.33

# Minimum PA for a reliever to be included in team aggregate
MIN_RP_PA = 30

FIELDS = [
    "team", "bullpen_pa", "bullpen_woba", "bullpen_xwoba", "bullpen_gap",
    "bullpen_hard_hit", "bullpen_k_pct", "pitchers_counted", "fetched_at"
]

# Standard abbreviation normalization
ABBREV_FIX = {
    "TB":  "TBR", "KC":  "KCR", "SD":  "SDP",
    "SF":  "SFG", "AZ":  "ARI", "WAS": "WSH",
}


def fetch_mlb_pitchers(year: int) -> dict[int, dict]:
    """
    Fetch all pitchers from MLB Stats API.
    Returns dict keyed by player_id with team, gamesStarted, gamesPitched.
    Handles pagination automatically.
    """
    pitchers = {}
    offset = 0
    limit  = 500

    while True:
        url = (
            "https://statsapi.mlb.com/api/v1/stats"
            f"?stats=season&group=pitching&season={year}"
            f"&gameType=R&playerPool=All&limit={limit}&offset={offset}"
        )
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"  [!] MLB Stats API error at offset {offset}: {e}")
            break

        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            break

        for s in splits:
            pid   = s.get("player", {}).get("id")
            team  = s.get("team", {}).get("abbreviation", "")
            team  = ABBREV_FIX.get(team, team)
            stat  = s.get("stat", {})
            gs    = int(stat.get("gamesStarted", 0) or 0)
            gp    = int(stat.get("gamesPitched", 0) or 0)
            ip    = float(stat.get("inningsPitched", 0) or 0)

            if pid and team:
                pitchers[pid] = {
                    "team":         team,
                    "gamesStarted": gs,
                    "gamesPitched": gp,
                    "inningsPitched": ip,
                    "is_reliever":  (gs / gp < RP_THRESHOLD) if gp > 0 else True,
                }

        if len(splits) < limit:
            break
        offset += limit
        time.sleep(0.3)

    return pitchers


def load_savant_stats(path: Path) -> dict[int, dict]:
    """
    Load pitcher Statcast data from stats.csv.
    Returns dict keyed by player_id (2026 rows only, best PA row per player).
    """
    savant = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    reader = csv.reader(io.StringIO(content))
    headers = [h.strip().strip('"') for h in next(reader)]

    for row in reader:
        d = dict(zip(headers, row))
        if d.get("year") != str(YEAR):
            continue
        try:
            pid = int(d.get("player_id", 0))
            pa  = int(d.get("pa", 0) or 0)
            if not pid or pa < MIN_RP_PA:
                continue
            if pid in savant and pa <= savant[pid]["pa"]:
                continue
            savant[pid] = {
                "name":      d.get("last_name, first_name", ""),
                "pa":        pa,
                "woba":      float(d.get("woba", 0) or 0),
                "xwoba":     float(d.get("xwoba", 0) or 0),
                "gap":       round(float(d.get("woba", 0) or 0) -
                                   float(d.get("xwoba", 0) or 0), 4),
                "hard_hit":  float(d.get("hard_hit_percent", 0) or 0),
                "k_pct":     float(d.get("k_percent", 0) or 0),
            }
        except (ValueError, KeyError):
            continue

    return savant


def build_bullpen(mlb: dict, savant: dict) -> list[dict]:
    """
    Merge MLB role/team data with Savant xwOBA data.
    Aggregate by team for relievers only.
    """
    # Match by player_id
    team_buckets: dict[str, list] = {}

    matched = skipped_not_rp = skipped_no_savant = 0

    for pid, mlb_data in mlb.items():
        if not mlb_data["is_reliever"]:
            skipped_not_rp += 1
            continue

        svt = savant.get(pid)
        if not svt:
            skipped_no_savant += 1
            continue

        team = mlb_data["team"]
        if team not in team_buckets:
            team_buckets[team] = []
        team_buckets[team].append(svt)
        matched += 1

    print(f"  Matched: {matched} relievers | "
          f"Skipped (starters): {skipped_not_rp} | "
          f"No Savant data: {skipped_no_savant}")

    # Aggregate per team (PA-weighted averages)
    rows = []
    for team, relievers in sorted(team_buckets.items()):
        total_pa   = sum(r["pa"] for r in relievers)
        if total_pa == 0:
            continue

        def wavg(field):
            return round(
                sum(r[field] * r["pa"] for r in relievers) / total_pa, 4
            )

        rows.append({
            "team":              team,
            "bullpen_pa":        total_pa,
            "bullpen_woba":      wavg("woba"),
            "bullpen_xwoba":     wavg("xwoba"),
            "bullpen_gap":       round(wavg("woba") - wavg("xwoba"), 4),
            "bullpen_hard_hit":  wavg("hard_hit"),
            "bullpen_k_pct":     wavg("k_pct"),
            "pitchers_counted":  len(relievers),
            "fetched_at":        datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        })

    return rows


def main():
    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 55)
    print(f"  FETCH BULLPEN — {fetched_at}")
    print("=" * 55)

    # Step 1: MLB Stats API for roles + teams
    print(f"\n  [1/3] Fetching pitcher roles from MLB Stats API...",
          end="", flush=True)
    mlb = fetch_mlb_pitchers(YEAR)
    rp_count = sum(1 for p in mlb.values() if p["is_reliever"])
    print(f" {len(mlb)} pitchers ({rp_count} relievers)")

    if not mlb:
        print("  [!] No MLB data — exiting")
        sys.exit(1)

    # Step 2: Load Savant stats
    print(f"  [2/3] Loading Savant stats from {STATS_FILE}...",
          end="", flush=True)
    if not STATS_FILE.exists():
        print(f"\n  [!] {STATS_FILE} not found — run fetch_stats.py first")
        sys.exit(1)

    savant = load_savant_stats(STATS_FILE)
    print(f" {len(savant)} pitchers with PA >= {MIN_RP_PA}")

    # Step 3: Merge and aggregate
    print(f"  [3/3] Building team bullpen aggregates...")
    rows = build_bullpen(mlb, savant)

    # Save
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [✓] Saved: {OUT_FILE} ({len(rows)} teams)")
    print()
    print(f"  {'Team':<6} {'PA':>6} {'wOBA':>7} {'xwOBA':>7} "
          f"{'Gap':>7} {'HH%':>6} {'K%':>6} {'RPs':>5}")
    print("  " + "─" * 54)
    for r in sorted(rows, key=lambda x: x["bullpen_gap"]):
        trend = "↑IMPROVE" if r["bullpen_gap"] > 0.015 else \
                ("↓REGRESS" if r["bullpen_gap"] < -0.015 else "neutral")
        print(f"  {r['team']:<6} {r['bullpen_pa']:>6} "
              f"{r['bullpen_woba']:>7.3f} {r['bullpen_xwoba']:>7.3f} "
              f"{r['bullpen_gap']:>+7.3f} {r['bullpen_hard_hit']:>6.1f} "
              f"{r['bullpen_k_pct']:>6.1f} {r['pitchers_counted']:>5}  {trend}")
    print()
    print("=" * 55)


if __name__ == "__main__":
    main()
