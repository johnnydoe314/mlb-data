#!/usr/bin/env python3
"""
fetch_bullpen.py v4
===================
Root cause fix: /v1/stats?teamId={id} only returns qualified pitchers
(those with enough IP/GS), which filters out most relievers.

Solution: use the 40-man ROSTER endpoint + hydrate stats.
  GET /v1/teams/{id}/roster?rosterType=40Man&hydrate=stats(...)
  → returns ALL pitchers on roster including low-leverage relievers
  → classify by position (P + GS ratio) and match to Savant xwOBA data
"""

import csv, io, json, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR    = Path("data")
OUT_FILE   = OUT_DIR / "bullpen.csv"
STATS_FILE = Path("data/stats.csv")
YEAR       = 2026
TIMEOUT    = 20
RP_THRESH  = 0.33   # GS/GP < this → reliever
MIN_RP_PA  = 30     # min PA in Savant to include

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


def fetch_all_teams():
    data = get(
        f"https://statsapi.mlb.com/api/v1/teams"
        f"?sportId=1&season={YEAR}&activeStatus=Y"
    )
    return [
        {"id": t["id"], "abbrev": ABBREV_FIX.get(t["abbreviation"], t["abbreviation"])}
        for t in data.get("teams", [])
    ]


def fetch_team_pitchers_via_roster(team_id: int, abbrev: str) -> list[dict]:
    """
    Use 40-man roster with hydrated pitching stats.
    Returns ALL pitchers on roster, not just qualified ones.
    """
    url = (
        f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
        f"?rosterType=40Man&season={YEAR}"
        f"&hydrate=stats(group=pitching,type=season,season={YEAR},gameType=R)"
    )
    try:
        data = get(url)
    except Exception as e:
        print(f"    Error fetching roster: {e}")
        return []

    roster = data.get("roster", [])
    pitchers = []

    for player in roster:
        # Only pitchers
        pos = player.get("position", {}).get("abbreviation", "")
        if pos != "P":
            continue

        pid  = player.get("person", {}).get("id")
        if not pid:
            continue

        # Get hydrated stats
        stats_list = player.get("person", {}).get("stats", [])
        if not stats_list:
            # Some players may not have stats yet
            continue

        stat = stats_list[0].get("stats", {}) if stats_list else {}
        gs   = int(stat.get("gamesStarted", 0) or 0)
        gp   = int(stat.get("gamesPlayed", stat.get("gamesPitched", 0)) or 0)

        if gp == 0:
            continue

        is_rp = (gs / gp) < RP_THRESH
        pitchers.append({
            "player_id":    pid,
            "gamesStarted": gs,
            "gamesPlayed":  gp,
            "is_reliever":  is_rp,
        })

    return pitchers


def load_savant(path):
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


def wavg(rps, field):
    total = sum(r["pa"] for r in rps)
    return round(sum(r[field] * r["pa"] for r in rps) / total, 4) if total else 0.0


def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print("=" * 58)
    print(f"  FETCH BULLPEN v4 — {ts}")
    print("=" * 58)

    # 1. Load Savant
    print(f"\n  [1/3] Loading {STATS_FILE}...", end="", flush=True)
    if not STATS_FILE.exists():
        print(f"\n  [!] Not found — run fetch_stats.py first")
        sys.exit(1)
    savant = load_savant(STATS_FILE)
    print(f" {len(savant)} pitchers (PA ≥ {MIN_RP_PA})")

    # 2. Get teams
    print(f"  [2/3] Fetching team list...", end="", flush=True)
    try:
        teams = fetch_all_teams()
    except Exception as e:
        print(f"\n  [!] {e}")
        sys.exit(1)
    print(f" {len(teams)} teams")

    # 3. Roster + stats per team
    print(f"  [3/3] Fetching 40-man roster + pitching stats per team...")

    # Debug first team
    first = teams[0]
    debug_url = (
        f"https://statsapi.mlb.com/api/v1/teams/{first['id']}/roster"
        f"?rosterType=40Man&season={YEAR}"
        f"&hydrate=stats(group=pitching,type=season,season={YEAR},gameType=R)"
    )
    try:
        debug_data = get(debug_url)
        roster = debug_data.get("roster", [])
        pitchers_on_roster = [p for p in roster if p.get("position",{}).get("abbreviation") == "P"]
        print(f"  DEBUG {first['abbrev']}: {len(roster)} roster, {len(pitchers_on_roster)} pitchers")
        if pitchers_on_roster:
            p = pitchers_on_roster[0]
            pid = p.get("person",{}).get("id")
            name = p.get("person",{}).get("fullName","?")
            stats_list = p.get("person",{}).get("stats",[])
            has_stats = "YES" if stats_list else "NO"
            print(f"  DEBUG first pitcher: {name} (id={pid}) has_stats={has_stats}")
            if stats_list:
                stat = stats_list[0].get("stats",{})
                print(f"  DEBUG stat keys: {list(stat.keys())[:12]}")
                print(f"  DEBUG gamesPlayed={stat.get('gamesPlayed','?')} "
                      f"gamesStarted={stat.get('gamesStarted','?')}")
    except Exception as e:
        print(f"  DEBUG error: {e}")
    print()

    team_buckets = {}
    total_matched = 0

    for t in teams:
        pitchers = fetch_team_pitchers_via_roster(t["id"], t["abbrev"])
        matched = 0
        rp_count = 0

        for p in pitchers:
            if not p["is_reliever"]:
                continue
            rp_count += 1
            svt = savant.get(p["player_id"])
            if not svt:
                continue
            team_buckets.setdefault(t["abbrev"], []).append(svt)
            matched += 1
            total_matched += 1

        total_p = len(pitchers)
        sp_count = total_p - rp_count
        print(f"    {t['abbrev']:<5} {total_p:>3} pitchers "
              f"({sp_count} SP / {rp_count} RP) → {matched} Savant matches")
        time.sleep(0.15)

    print(f"\n  Total: {total_matched} reliever-Savant matches")

    if not team_buckets:
        print("  [!] Still no data — check DEBUG output above")
        # Don't exit with error — partial data is OK
        sys.exit(1)

    # Aggregate
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
        flag = " ↑LUCKY" if r["bullpen_gap"] > 0.015 else \
               (" ↓UNLUCKY" if r["bullpen_gap"] < -0.015 else "")
        print(f"  {r['team']:<6} {r['bullpen_pa']:>6} "
              f"{r['bullpen_woba']:>7.3f} {r['bullpen_xwoba']:>7.3f} "
              f"{r['bullpen_gap']:>+7.4f} {r['bullpen_hard_hit']:>6.1f} "
              f"{r['bullpen_k_pct']:>6.1f} {r['pitchers_counted']:>4}{flag}")
    print("=" * 58)


if __name__ == "__main__":
    main()
