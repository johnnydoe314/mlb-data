#!/usr/bin/env python3
"""
fetch_platoon_splits.py
=======================
Fetches team batting splits vs LHP and RHP from Baseball Savant.
Also builds a pitcher handedness cache from the MLB Stats API
using pitcher IDs in probable_pitchers.csv.

Outputs:
  data/team_platoon.csv    — team off_score vs LHP / vs RHP
  data/pitcher_hand.csv    — pitcher_id → hand (L/R)
"""

import csv, io, json, sys, time, urllib.request
from datetime import datetime
from pathlib import Path
from collections import defaultdict

OUT_DIR        = Path("data")
PLATOON_OUT    = OUT_DIR / "team_platoon.csv"
HAND_OUT       = OUT_DIR / "pitcher_hand.csv"
YEAR           = "2026"
TIMEOUT        = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Referer": "https://baseballsavant.mlb.com/",
}

SAVANT_NORM = {
    "Arizona Diamondbacks":"ARI","Atlanta Braves":"ATL","Baltimore Orioles":"BAL",
    "Boston Red Sox":"BOS","Chicago Cubs":"CHC","Chicago White Sox":"CWS",
    "Cincinnati Reds":"CIN","Cleveland Guardians":"CLE","Colorado Rockies":"COL",
    "Detroit Tigers":"DET","Houston Astros":"HOU","Kansas City Royals":"KCR",
    "Los Angeles Angels":"LAA","Los Angeles Dodgers":"LAD","Miami Marlins":"MIA",
    "Milwaukee Brewers":"MIL","Minnesota Twins":"MIN","New York Mets":"NYM",
    "New York Yankees":"NYY","Oakland Athletics":"ATH","Philadelphia Phillies":"PHI",
    "Pittsburgh Pirates":"PIT","San Diego Padres":"SDP","San Francisco Giants":"SFG",
    "Seattle Mariners":"SEA","St. Louis Cardinals":"STL","Tampa Bay Rays":"TBR",
    "Texas Rangers":"TEX","Toronto Blue Jays":"TOR","Washington Nationals":"WSH",
    "ARI":"ARI","ATL":"ATL","BAL":"BAL","BOS":"BOS","CHC":"CHC","CWS":"CWS",
    "CIN":"CIN","CLE":"CLE","COL":"COL","DET":"DET","HOU":"HOU","KC":"KCR",
    "KCR":"KCR","LAA":"LAA","LAD":"LAD","MIA":"MIA","MIL":"MIL","MIN":"MIN",
    "NYM":"NYM","NYY":"NYY","OAK":"ATH","PHI":"PHI","PIT":"PIT","SD":"SDP",
    "SDP":"SDP","SF":"SFG","SFG":"SFG","SEA":"SEA","STL":"STL","TB":"TBR",
    "TBR":"TBR","TEX":"TEX","TOR":"TOR","WSH":"WSH","AZ":"ARI",
}

PLATOON_FIELDS = [
    "team",
    "pa_vs_lhp","xwoba_vs_lhp","bb_pct_vs_lhp","hard_hit_vs_lhp","off_score_vs_lhp",
    "pa_vs_rhp","xwoba_vs_rhp","bb_pct_vs_rhp","hard_hit_vs_rhp","off_score_vs_rhp",
]

LG_XWOBA = 0.318; LG_BB = 8.5; LG_HH = 37.0

def calc_off_score(xwoba, bb_pct, hard_hit_pct):
    bb_norm = LG_XWOBA + (bb_pct - LG_BB) * 0.006
    hh_norm = LG_XWOBA + (hard_hit_pct - LG_HH) * 0.003
    return round(xwoba * 0.55 + bb_norm * 0.25 + hh_norm * 0.20, 4)


def _fetch(url):
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            if attempt < 3:
                time.sleep(attempt * 5)
            else:
                raise


def _fetch_splits(hand: str) -> dict:
    """
    Fetch team batting stats vs LHP (hand='L') or RHP (hand='R').
    Returns dict keyed by team abbreviation.
    """
    # Method 1: Savant leaderboard group_by=team with pitchHand filter
    urls = [
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={YEAR}&type=batter&group_by=team&filter=&min=0"
            "&selections=pa%2Cwoba%2Cxwoba%2Chard_hit_percent%2Cbb_percent%2Ck_percent"
            f"&sort=xwoba&sortDir=desc&csv=true&pitchHand={hand}"
        ),
        # Method 2: statcast_search group_by=team with pitch_hand filter
        (
            "https://baseballsavant.mlb.com/statcast_search/csv"
            f"?all=true&hfGT=R%7C&hfSea={YEAR}%7C"
            f"&player_type=batter&group_by=team&pitchHand={hand}&min_results=0"
        ),
    ]

    for i, url in enumerate(urls, 1):
        try:
            content = _fetch(url)
            rows = _parse_savant_splits(content)
            if len(rows) >= 20:
                print(f"    [✓] vs {'LHP' if hand=='L' else 'RHP'} method {i}: {len(rows)} teams")
                return rows
            print(f"    [~] Method {i}: {len(rows)} teams")
        except Exception as e:
            print(f"    [~] Method {i} failed: {e}")

    # Method 3: Aggregate from individual batter leaderboard
    print(f"    [~] Falling back to individual batter aggregation vs {'LHP' if hand=='L' else 'RHP'}")
    return _fetch_and_aggregate(hand)


def _parse_savant_splits(content: str) -> dict:
    reader  = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    orig    = reader.fieldnames or []
    col_map = {c.lower(): c for c in orig}

    def g(row, *names):
        for n in names:
            v = row.get(col_map.get(n,""), "")
            if v not in ("",None): return v
        return "0"

    team_col = next((col_map.get(c) for c in
        ["team_name","abbreviation","team","club"] if col_map.get(c)), None)

    result = {}
    for row in reader:
        raw  = row.get(team_col or "","").strip()
        team = SAVANT_NORM.get(raw) or SAVANT_NORM.get(raw.upper())
        if not team: continue
        try:
            result[team] = {
                "pa":       int(float(g(row,"pa","plate_appearances") or 0)),
                "xwoba":    float(g(row,"xwoba") or 0),
                "bb_pct":   float(g(row,"bb_percent","bb_pct") or 0),
                "hard_hit": float(g(row,"hard_hit_percent","hardhit_percent") or 0),
            }
        except (ValueError, TypeError):
            continue
    return result


def _fetch_and_aggregate(hand: str) -> dict:
    """Fetch individual batter leaderboard and aggregate by team."""
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={YEAR}&type=batter&filter=&min=10"
        "&selections=team_name%2Cpa%2Cwoba%2Cxwoba%2Chard_hit_percent%2Cbb_percent%2Ck_percent"
        f"&sort=xwoba&sortDir=desc&csv=true&pitchHand={hand}&player_type=batter"
    )
    try:
        content = _fetch(url)
    except Exception as e:
        print(f"    [✗] Individual aggregation failed: {e}")
        return {}

    reader  = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    orig    = reader.fieldnames or []
    col_map = {c.lower(): c for c in orig}

    def g(row, *names):
        for n in names:
            v = row.get(col_map.get(n,""), "")
            if v not in ("",None): return v
        return "0"

    team_col = next((col_map.get(c) for c in
        ["team_name","team","abbreviation"] if col_map.get(c)), None)

    # Weighted aggregation by PA
    totals = defaultdict(lambda: {"pa":0,"xwoba_sum":0,"bb_sum":0,"hh_sum":0})
    for row in reader:
        raw  = row.get(team_col or "","").strip()
        team = SAVANT_NORM.get(raw) or SAVANT_NORM.get(raw.upper())
        if not team: continue
        try:
            pa      = int(float(g(row,"pa") or 0))
            xwoba   = float(g(row,"xwoba") or 0)
            bb_pct  = float(g(row,"bb_percent","bb_pct") or 0)
            hard    = float(g(row,"hard_hit_percent","hardhit_percent") or 0)
            if pa < 1: continue
            t = totals[team]
            t["pa"]        += pa
            t["xwoba_sum"] += xwoba   * pa
            t["bb_sum"]    += bb_pct  * pa
            t["hh_sum"]    += hard    * pa
        except (ValueError, TypeError):
            continue

    result = {}
    for team, t in totals.items():
        if t["pa"] < 50: continue
        result[team] = {
            "pa":       t["pa"],
            "xwoba":    round(t["xwoba_sum"] / t["pa"], 4),
            "bb_pct":   round(t["bb_sum"]    / t["pa"], 2),
            "hard_hit": round(t["hh_sum"]    / t["pa"], 2),
        }
    return result


def fetch_pitcher_handedness():
    """
    Build pitcher_hand.csv by reading probable_pitchers.csv and
    querying MLB Stats API for each unique pitcher ID not already cached.
    """
    prob_file = OUT_DIR / "probable_pitchers.csv"
    if not prob_file.exists():
        print("  [!] probable_pitchers.csv not found — skipping hand cache")
        return

    # Load existing cache
    cache = {}
    if HAND_OUT.exists():
        for row in csv.DictReader(open(HAND_OUT, encoding="utf-8")):
            pid = row.get("pitcher_id","").strip()
            if pid: cache[pid] = {"name": row["name"], "hand": row["hand"]}

    # Collect new IDs
    new_ids = set()
    for row in csv.DictReader(open(prob_file, encoding="utf-8")):
        for col in ("away_pitcher_id","home_pitcher_id"):
            pid = row.get(col,"").strip()
            if pid and pid not in cache: new_ids.add(pid)

    if new_ids:
        print(f"  Looking up handedness for {len(new_ids)} pitchers...")
        # Batch requests of 50
        id_list = list(new_ids)
        for i in range(0, len(id_list), 50):
            batch = ",".join(id_list[i:i+50])
            url   = (
                f"https://statsapi.mlb.com/api/v1/people?personIds={batch}"
                "&fields=people,id,fullName,pitchHand"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent":"mlb/1.0"})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    data = json.loads(r.read())
                for person in data.get("people",[]):
                    pid  = str(person.get("id",""))
                    name = person.get("fullName","")
                    hand = person.get("pitchHand",{}).get("code","R")
                    cache[pid] = {"name": name, "hand": hand}
            except Exception as e:
                print(f"  [!] Batch lookup failed: {e}")
            time.sleep(0.3)

    # Write cache
    with open(HAND_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["pitcher_id","name","hand"])
        writer.writeheader()
        for pid, v in sorted(cache.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
            writer.writerow({"pitcher_id":pid, "name":v["name"], "hand":v["hand"]})

    print(f"  [✓] {HAND_OUT} ({len(cache)} pitchers)")


def main():
    print(f"Fetching platoon splits {YEAR}...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    lhp = _fetch_splits("L")
    rhp = _fetch_splits("R")

    all_teams = sorted(set(list(lhp.keys()) + list(rhp.keys())))
    rows = []
    for team in all_teams:
        l = lhp.get(team, {"pa":0,"xwoba":LG_XWOBA,"bb_pct":LG_BB,"hard_hit":LG_HH})
        r = rhp.get(team, {"pa":0,"xwoba":LG_XWOBA,"bb_pct":LG_BB,"hard_hit":LG_HH})
        rows.append({
            "team":             team,
            "pa_vs_lhp":        l["pa"],
            "xwoba_vs_lhp":     l["xwoba"],
            "bb_pct_vs_lhp":    l["bb_pct"],
            "hard_hit_vs_lhp":  l["hard_hit"],
            "off_score_vs_lhp": calc_off_score(l["xwoba"], l["bb_pct"], l["hard_hit"]),
            "pa_vs_rhp":        r["pa"],
            "xwoba_vs_rhp":     r["xwoba"],
            "bb_pct_vs_rhp":    r["bb_pct"],
            "hard_hit_vs_rhp":  r["hard_hit"],
            "off_score_vs_rhp": calc_off_score(r["xwoba"], r["bb_pct"], r["hard_hit"]),
        })

    with open(PLATOON_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=PLATOON_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)

    print(f"  [✓] {PLATOON_OUT} ({len(rows)} teams)")
    for r in rows[:3]:
        print(f"    {r['team']:<5} vs LHP off:{r['off_score_vs_lhp']:.4f}"
              f"  vs RHP off:{r['off_score_vs_rhp']:.4f}")

    # Build pitcher handedness cache
    print("\nBuilding pitcher handedness cache...")
    fetch_pitcher_handedness()
    print("\nDone.")


if __name__ == "__main__":
    main()
