#!/usr/bin/env python3
"""
fetch_bullpen.py v5
===================
Two-step approach (most reliable):
  Step 1: GET /v1/teams/{id}/roster?rosterType=40Man
          → all pitcher IDs + team affiliations (30 calls, no stats needed)
  Step 2: GET /v1/people?personIds=id1,id2,...
          &hydrate=stats(group=pitching,type=season,season=2026,gameType=R)
          → batch stats for all pitchers (batches of 50, ~18 calls)

This avoids the broken hydrate-on-roster syntax and uses
the well-documented /v1/people endpoint instead.
"""

import csv, io, json, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR    = Path("data")
OUT_FILE   = OUT_DIR / "bullpen.csv"
STATS_FILE = Path("data/stats.csv")
YEAR       = 2026
TIMEOUT    = 20
RP_THRESH  = 0.33
MIN_RP_PA  = 30
BATCH_SIZE = 50   # personIds per API call

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


def get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())


# ── Step 1: get all pitcher IDs from rosters ──────────────────────────────────

def fetch_all_teams():
    data = get(f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={YEAR}&activeStatus=Y")
    return [
        {"id": t["id"], "abbrev": ABBREV_FIX.get(t["abbreviation"], t["abbreviation"])}
        for t in data.get("teams", [])
    ]


def fetch_roster_pitcher_ids(team_id: int) -> list[int]:
    """Returns pitcher player IDs from 40-man roster (no stats)."""
    url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
        f"?rosterType=40Man&season={YEAR}"
    )
    try:
        data = get(url)
    except Exception as e:
        print(f"    roster error: {e}")
        return []
    return [
        p["person"]["id"]
        for p in data.get("roster", [])
        if p.get("position", {}).get("abbreviation") == "P"
        and p.get("person", {}).get("id")
    ]


# ── Step 2: batch-fetch stats via /v1/people ──────────────────────────────────

def fetch_people_stats(player_ids: list[int]) -> dict[int, dict]:
    """
    Fetch season pitching stats for a batch of player IDs.
    Returns {player_id: {gamesStarted, gamesPlayed, is_reliever}}
    """
    if not player_ids:
        return {}
    ids_str = ",".join(str(pid) for pid in player_ids)
    url = (
        f"https://statsapi.mlb.com/api/v1/people"
        f"?personIds={ids_str}"
        f"&hydrate=stats(group=pitching,type=season,season={YEAR},gameType=R)"
    )
    try:
        data = get(url)
    except Exception as e:
        print(f"    people/stats error: {e}")
        return {}

    result = {}
    for person in data.get("people", []):
        pid = person.get("id")
        if not pid:
            continue
        for stat_group in person.get("stats", []):
            grp = stat_group.get("group", {}).get("displayName", "").lower()
            if "pitching" not in grp:
                continue
            splits = stat_group.get("splits", [])
            if not splits:
                continue
            stat = splits[0].get("stat", {})
            gs   = int(stat.get("gamesStarted", 0) or 0)
            gp   = int(stat.get("gamesPlayed", stat.get("gamesPitched", 0)) or 0)
            if gp > 0:
                result[pid] = {
                    "gamesStarted": gs,
                    "gamesPlayed":  gp,
                    "is_reliever":  (gs / gp) < RP_THRESH,
                }
    return result


# ── Savant loader ─────────────────────────────────────────────────────────────

def load_savant(path):
    savant = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    reader = csv.reader(io.StringIO(content))
    hdrs = [h.strip().strip('"') for h in next(reader)]
    for row in reader:
        d = dict(zip(hdrs, row))
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


def wavg(rps, field):
    total = sum(r["pa"] for r in rps)
    return round(sum(r[field] * r["pa"] for r in rps) / total, 4) if total else 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 58)
    print(f"  FETCH BULLPEN v5 — {ts}")
    print("=" * 58)

    # 1. Savant
    print(f"\n  [1/4] Loading {STATS_FILE}...", end="", flush=True)
    if not STATS_FILE.exists():
        print(f"\n  [!] Not found — run fetch_stats.py first")
        sys.exit(1)
    savant = load_savant(STATS_FILE)
    print(f" {len(savant)} pitchers (PA ≥ {MIN_RP_PA})")

    # 2. Teams
    print(f"  [2/4] Fetching teams...", end="", flush=True)
    teams = fetch_all_teams()
    print(f" {len(teams)} teams")

    # 3. Roster IDs per team
    print(f"  [3/4] Fetching 40-man rosters (pitcher IDs only)...")
    pitcher_team_map: dict[int, str] = {}   # player_id → team abbrev
    for t in teams:
        ids = fetch_roster_pitcher_ids(t["id"])
        for pid in ids:
            pitcher_team_map[pid] = t["abbrev"]
        print(f"    {t['abbrev']:<5} {len(ids)} pitchers on 40-man")
        time.sleep(0.1)

    print(f"\n  Total pitchers across all rosters: {len(pitcher_team_map)}")

    # 4. Batch-fetch stats
    print(f"\n  [4/4] Fetching season stats in batches of {BATCH_SIZE}...")
    all_ids   = list(pitcher_team_map.keys())
    all_stats: dict[int, dict] = {}

    for i in range(0, len(all_ids), BATCH_SIZE):
        batch = all_ids[i : i + BATCH_SIZE]
        batch_stats = fetch_people_stats(batch)
        all_stats.update(batch_stats)
        found = len(batch_stats)
        print(f"    Batch {i//BATCH_SIZE + 1}: {len(batch)} IDs → {found} with stats")
        if i == 0 and found > 0:
            # Debug: show first result
            first_id = next(iter(batch_stats))
            s = batch_stats[first_id]
            print(f"    DEBUG first: id={first_id} "
                  f"GP={s['gamesPlayed']} GS={s['gamesStarted']} "
                  f"isRP={s['is_reliever']}")
        time.sleep(0.2)

    # 5. Build team buckets
    team_buckets: dict[str, list] = {}
    sp_count = rp_count = matched = 0

    for pid, role in all_stats.items():
        team = pitcher_team_map.get(pid)
        if not team:
            continue
        if not role["is_reliever"]:
            sp_count += 1
            continue
        rp_count += 1
        svt = savant.get(pid)
        if not svt:
            continue
        team_buckets.setdefault(team, []).append(svt)
        matched += 1

    print(f"\n  Classified: {sp_count} starters / {rp_count} relievers")
    print(f"  Savant matches: {matched}")

    if not team_buckets:
        print("  [!] No matches — check batches above")
        sys.exit(1)

    # 6. Aggregate and save
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

    print(f"\n  [✓] {OUT_FILE} — {len(rows)} teams")
    print()
    print(f"  {'Team':<6} {'PA':>6} {'wOBA':>7} {'xwOBA':>7} "
          f"{'Gap':>7} {'HH%':>6} {'K%':>6} {'RPs':>4}")
    print("  " + "─" * 52)
    for r in sorted(rows, key=lambda x: x["bullpen_gap"]):
        flag = " ↑LUCKY"   if r["bullpen_gap"] > 0.015 else \
               " ↓UNLUCKY" if r["bullpen_gap"] < -0.015 else ""
        print(f"  {r['team']:<6} {r['bullpen_pa']:>6} "
              f"{r['bullpen_woba']:>7.3f} {r['bullpen_xwoba']:>7.3f} "
              f"{r['bullpen_gap']:>+7.4f} {r['bullpen_hard_hit']:>6.1f} "
              f"{r['bullpen_k_pct']:>6.1f} {r['pitchers_counted']:>4}{flag}")
    print("=" * 58)


if __name__ == "__main__":
    main()
