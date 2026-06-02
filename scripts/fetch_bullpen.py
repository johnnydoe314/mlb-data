#!/usr/bin/env python3
"""
fetch_bullpen.py v3
===================
Fixed: /v1/teams/{id}/stats returns team aggregate (1 row).
Fixed: /v1/stats?teamId={id} returns individual pitcher rows.
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
    data = get(f"https://statsapi.mlb.com/api/v1/teams?sportId=1&season={YEAR}&activeStatus=Y")
    return [
        {"id": t["id"], "abbrev": ABBREV_FIX.get(t["abbreviation"], t["abbreviation"])}
        for t in data.get("teams", [])
    ]


def fetch_team_pitchers(team_id: int) -> list[dict]:
    """
    FIXED: Use /v1/stats?teamId={id} — returns individual player rows.
    /v1/teams/{id}/stats returns only team aggregate (was always 1 row).
    """
    url = (
        f"https://statsapi.mlb.com/api/v1/stats"
        f"?stats=season&group=pitching&season={YEAR}"
        f"&teamId={team_id}&gameType=R&limit=100"
    )
    try:
        data = get(url)
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code} for team {team_id}")
        return []
    except Exception as e:
        print(f"    Error: {e}")
        return []

    splits = data.get("stats", [{}])[0].get("splits", [])

    # Debug: show first call response structure
    if team_id == -1:  # won't trigger, just for reference
        print(f"    DEBUG splits[0] keys: {list(splits[0].keys()) if splits else 'empty'}")

    pitchers = []
    for s in splits:
        pid  = s.get("player", {}).get("id")
        stat = s.get("stat", {})
        gs   = int(stat.get("gamesStarted", 0) or 0)
        gp   = int(stat.get("gamesPlayed", 0) or 0)
        if pid and gp > 0:
            pitchers.append({
                "player_id":   pid,
                "gamesStarted": gs,
                "gamesPitched": gp,
                "is_reliever": (gs / gp) < RP_THRESH,
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
    print(f"  FETCH BULLPEN v3 — {ts}")
    print("=" * 58)

    print(f"\n  [1/3] Loading {STATS_FILE}...", end="", flush=True)
    if not STATS_FILE.exists():
        print(f"\n  [!] Not found — run fetch_stats.py first")
        sys.exit(1)
    savant = load_savant(STATS_FILE)
    print(f" {len(savant)} pitchers (PA ≥ {MIN_RP_PA})")

    print(f"  [2/3] Fetching team list...", end="", flush=True)
    try:
        teams = fetch_all_teams()
    except Exception as e:
        print(f"\n  [!] {e}")
        sys.exit(1)
    print(f" {len(teams)} teams")

    print(f"  [3/3] Fetching individual pitcher stats per team...")

    # Debug: show raw response from first team
    first_team = teams[0]
    debug_url = (
        f"https://statsapi.mlb.com/api/v1/stats"
        f"?stats=season&group=pitching&season={YEAR}"
        f"&teamId={first_team['id']}&gameType=R&limit=5"
    )
    try:
        debug_data = get(debug_url)
        splits = debug_data.get("stats", [{}])[0].get("splits", [])
        print(f"  DEBUG {first_team['abbrev']} (id={first_team['id']}): "
              f"{len(splits)} splits returned")
        if splits:
            s = splits[0]
            print(f"  DEBUG split keys: {list(s.keys())}")
            print(f"  DEBUG player: {s.get('player',{}).get('fullName','?')} "
                  f"id={s.get('player',{}).get('id','?')}")
            print(f"  DEBUG stat keys: {list(s.get('stat',{}).keys())[:8]}")
        else:
            # Show full raw response structure
            print(f"  DEBUG raw keys: {list(debug_data.keys())}")
            stats_list = debug_data.get("stats", [])
            if stats_list:
                print(f"  DEBUG stats[0] keys: {list(stats_list[0].keys())}")
                print(f"  DEBUG splits count: {len(stats_list[0].get('splits',[]))}")
    except Exception as e:
        print(f"  DEBUG error: {e}")

    print()

    team_buckets = {}
    total_matched = 0

    for t in teams:
        pitchers = fetch_team_pitchers(t["id"])
        matched = 0
        for p in pitchers:
            if not p["is_reliever"]:
                continue
            svt = savant.get(p["player_id"])
            if not svt:
                continue
            team_buckets.setdefault(t["abbrev"], []).append(svt)
            matched += 1
            total_matched += 1

        rp_count = sum(1 for p in pitchers if p["is_reliever"])
        print(f"    {t['abbrev']:<5} {len(pitchers):>3} pitchers "
              f"({rp_count} RPs) → {matched} Savant matches")
        time.sleep(0.15)

    print(f"\n  Total: {total_matched} reliever-Savant matches")

    if not team_buckets:
        print("  [!] No data — check DEBUG output above for API response structure")
        sys.exit(1)

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
