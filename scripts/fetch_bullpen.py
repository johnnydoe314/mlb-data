#!/usr/bin/env python3
"""
fetch_bullpen.py v2
===================
Builds team-level bullpen xwOBA gap data.

Strategy (team-by-team approach — mirrors fetch_pitchers.py pattern):
  1. GET /v1/teams → all 30 MLB team IDs
  2. For each team: GET /v1/teams/{id}/stats?group=pitching → individual pitcher stats
     (includes player_id, gamesStarted, gamesPitched per pitcher)
  3. Load stats.csv (Savant) → xwOBA, wOBA, gap per pitcher
  4. Match by player_id, filter relievers (GS/G < 0.33), aggregate by team

Output: data/bullpen.csv
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

OUT_DIR    = Path("data")
OUT_FILE   = OUT_DIR / "bullpen.csv"
STATS_FILE = Path("data/stats.csv")
YEAR       = 2026
TIMEOUT    = 20
RP_THRESH  = 0.33   # GS/GP < this → reliever
MIN_RP_PA  = 30     # min PA to include in team aggregate

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

ABBREV_FIX = {
    "TB": "TBR", "KC": "KCR", "SD": "SDP",
    "SF": "SFG", "AZ": "ARI", "WAS": "WSH",
}

FIELDS = [
    "team", "bullpen_pa", "bullpen_woba", "bullpen_xwoba", "bullpen_gap",
    "bullpen_hard_hit", "bullpen_k_pct", "pitchers_counted", "fetched_at"
]


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_all_teams() -> list[dict]:
    """Get all 30 active MLB teams with their IDs."""
    data = get(
        f"https://statsapi.mlb.com/api/v1/teams"
        f"?sportId=1&season={YEAR}&activeStatus=Y"
    )
    return [
        {
            "id":   t["id"],
            "abbrev": ABBREV_FIX.get(t["abbreviation"], t["abbreviation"]),
        }
        for t in data.get("teams", [])
    ]


def fetch_team_pitching(team_id: int) -> list[dict]:
    """
    Get individual pitcher stats for one team.
    Returns list of {player_id, gamesStarted, gamesPitched, is_reliever}
    """
    url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/stats"
        f"?season={YEAR}&stats=season&group=pitching"
        f"&gameType=R&playerPool=All"
    )
    try:
        data = get(url)
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code} for team {team_id}")
        return []
    except Exception as e:
        print(f"    Error for team {team_id}: {e}")
        return []

    pitchers = []
    for split in data.get("stats", [{}])[0].get("splits", []):
        pid  = split.get("player", {}).get("id")
        stat = split.get("stat", {})
        gs   = int(stat.get("gamesStarted", 0) or 0)
        gp   = int(stat.get("gamesPitched", 0) or 0)
        if pid and gp > 0:
            pitchers.append({
                "player_id":    pid,
                "gamesStarted": gs,
                "gamesPitched": gp,
                "is_reliever":  (gs / gp) < RP_THRESH,
            })
    return pitchers


def load_savant(path: Path) -> dict[int, dict]:
    """Load stats.csv — keyed by player_id (2026 rows only)."""
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
                "pa":       pa,
                "woba":     float(d.get("woba", 0) or 0),
                "xwoba":    float(d.get("xwoba", 0) or 0),
                "gap":      round(float(d.get("woba", 0) or 0) -
                                  float(d.get("xwoba", 0) or 0), 4),
                "hard_hit": float(d.get("hard_hit_percent", 0) or 0),
                "k_pct":    float(d.get("k_percent", 0) or 0),
            }
        except (ValueError, KeyError):
            continue
    return savant


def wavg(relievers: list[dict], field: str) -> float:
    total_pa = sum(r["pa"] for r in relievers)
    if not total_pa:
        return 0.0
    return round(sum(r[field] * r["pa"] for r in relievers) / total_pa, 4)


def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 55)
    print(f"  FETCH BULLPEN v2 — {ts}")
    print("=" * 55)

    # ── 1. Load Savant stats (local file) ─────────────────────
    print(f"\n  [1/3] Loading {STATS_FILE}...", end="", flush=True)
    if not STATS_FILE.exists():
        print(f"\n  [!] {STATS_FILE} not found — run fetch_stats.py first")
        sys.exit(1)
    savant = load_savant(STATS_FILE)
    print(f" {len(savant)} pitchers (PA ≥ {MIN_RP_PA})")

    # ── 2. Get all teams ───────────────────────────────────────
    print(f"  [2/3] Fetching team list from MLB Stats API...",
          end="", flush=True)
    try:
        teams = fetch_all_teams()
    except Exception as e:
        print(f"\n  [!] Could not fetch teams: {e}")
        sys.exit(1)
    print(f" {len(teams)} teams")

    # ── 3. Fetch pitching stats team by team ───────────────────
    print(f"  [3/3] Fetching pitching stats per team...")
    team_buckets: dict[str, list] = {}
    total_matched = 0

    for t in teams:
        tid    = t["id"]
        abbrev = t["abbrev"]
        pitchers = fetch_team_pitching(tid)
        relievers_found = 0

        for p in pitchers:
            if not p["is_reliever"]:
                continue
            svt = savant.get(p["player_id"])
            if not svt:
                continue
            team_buckets.setdefault(abbrev, []).append(svt)
            relievers_found += 1
            total_matched += 1

        print(f"    {abbrev:<5} {len(pitchers):>3} pitchers → "
              f"{relievers_found} relievers matched to Savant")
        time.sleep(0.2)   # be respectful to the API

    print(f"\n  Total reliever-Savant matches: {total_matched}")

    if not team_buckets:
        print("  [!] No data built — check API responses above")
        sys.exit(1)

    # ── 4. Aggregate and save ──────────────────────────────────
    rows = []
    for team, rps in sorted(team_buckets.items()):
        rows.append({
            "team":             team,
            "bullpen_pa":       sum(r["pa"] for r in rps),
            "bullpen_woba":     wavg(rps, "woba"),
            "bullpen_xwoba":    wavg(rps, "xwoba"),
            "bullpen_gap":      round(wavg(rps, "woba") - wavg(rps, "xwoba"), 4),
            "bullpen_hard_hit": wavg(rps, "hard_hit"),
            "bullpen_k_pct":    wavg(rps, "k_pct"),
            "pitchers_counted": len(rps),
            "fetched_at":       ts,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  [✓] Saved {OUT_FILE} — {len(rows)} teams")
    print()
    print(f"  {'Team':<6} {'PA':>6} {'wOBA':>7} {'xwOBA':>7} "
          f"{'Gap':>7} {'HH%':>6} {'K%':>6} {'RPs':>4}")
    print("  " + "─" * 52)
    for r in sorted(rows, key=lambda x: x["bullpen_gap"]):
        flag = " ↑LUCKY" if r["bullpen_gap"] > 0.015 else \
               (" ↓UNLUCKY" if r["bullpen_gap"] < -0.015 else "")
        print(f"  {r['team']:<6} {r['bullpen_pa']:>6} "
              f"{r['bullpen_woba']:>7.3f} {r['bullpen_xwoba']:>7.3f} "
              f"{r['bullpen_gap']:>+7.4f} {r['bullpen_hard_hit']:>6.1f} "
              f"{r['bullpen_k_pct']:>6.1f} {r['pitchers_counted']:>4}{flag}")
    print()
    print("=" * 55)


if __name__ == "__main__":
    main()
