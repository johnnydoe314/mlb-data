#!/usr/bin/env python3
"""
fetch_statcast_hitting.py
=========================
Builds team_batting.csv with xwOBA (Savant), BB%/K% (MLB Stats API),
HH%/barrel (aggregated from statcast_batting.csv), off_score, and wrc_plus.

Sources:
  xwOBA, wOBA   → Baseball Savant group_by=team (primary) or MLB API fallback
  BB%, K%        → MLB Stats API (reliable, always available)
  HH%, barrel    → Aggregated from statcast_batting.csv individual batter data
  off_score      → xwOBA + BB% adj + HH% adj (additive, league-avg fallback)
  wRC+           → Approximated from wOBA with park factor
"""

import csv, io, json, sys, time, urllib.request, urllib.error
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OUT_DIR   = Path("data")
TEAM_OUT  = OUT_DIR / "team_batting.csv"
SC_FILE   = OUT_DIR / "statcast_batting.csv"   # individual batter data (already downloaded)
TIMEOUT   = 30
YEAR      = "2026"

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

MLB_ID = {
    109:"ARI",144:"ATL",110:"BAL",111:"BOS",112:"CHC",145:"CWS",
    113:"CIN",114:"CLE",115:"COL",116:"DET",117:"HOU",118:"KCR",
    108:"LAA",119:"LAD",146:"MIA",158:"MIL",142:"MIN",121:"NYM",
    147:"NYY",133:"ATH",143:"PHI",134:"PIT",135:"SDP",137:"SFG",
    136:"SEA",138:"STL",139:"TBR",140:"TEX",141:"TOR",120:"WSH",
}

TEAM_FIELDS = [
    "team","pa","xwoba","woba","hard_hit","barrel_pct","avg_ev",
    "bb_pct","k_pct","off_score","wrc_plus",
]

# ── Constants ─────────────────────────────────────────────────────────────────
LG_XWOBA = 0.318; LG_BB = 8.5; LG_HH = 37.0
WOBA_SCALE = 1.24; LG_R_PA = 0.119

PARK_FACTOR = {
    "COL":1.10,"BOS":1.05,"NYY":1.05,"CHC":1.03,"CIN":1.03,
    "TEX":1.01,"HOU":1.01,"SDP":0.97,"SFG":0.97,"TBR":0.99,
    "TOR":1.01,"MIN":1.01,
}

def calc_off_score(xwoba, bb_pct, hard_hit_pct):
    """
    Additive off_score: xwOBA as base, BB% and HH% adjust it.
    Missing/zero values fall back to league average → zero adjustment → off_score = xwOBA.
    """
    bb = bb_pct      if bb_pct      > 0 else LG_BB
    hh = hard_hit_pct if hard_hit_pct > 0 else LG_HH
    bb_adj = (bb - LG_BB) * 0.006 * 0.30   # each 1% BB above avg ≈ +0.0018 off_score
    hh_adj = (hh - LG_HH) * 0.003 * 0.20   # each 1% HH above avg ≈ +0.0006 off_score
    return round(xwoba + bb_adj + hh_adj, 4)

def calc_wrc_plus(woba, team):
    pf   = PARK_FACTOR.get(team, 1.00)
    rate = (woba - LG_XWOBA) / WOBA_SCALE + LG_R_PA
    return round((rate / LG_R_PA) * 100 / pf)


def _fetch(url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except Exception as e:
            if attempt < retries:
                time.sleep(attempt * 5)
            else:
                raise


# ── Source 1: Baseball Savant — xwOBA + wOBA ─────────────────────────────────
def fetch_savant_xwoba():
    urls = [
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={YEAR}&type=batter&group_by=team&filter=&min=0"
            "&selections=pa%2Cwoba%2Cxwoba%2Cexit_velocity_avg%2Cbarrel_batted_rate"
            "&sort=xwoba&sortDir=desc&csv=true"
        ),
        (
            "https://baseballsavant.mlb.com/statcast_search/csv"
            f"?all=true&hfGT=R%7C&hfSea={YEAR}%7C"
            "&player_type=batter&group_by=team&min_results=0"
        ),
    ]
    for i, url in enumerate(urls, 1):
        try:
            content = _fetch(url)
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
                xwoba = float(g(row,"xwoba") or 0)
                if xwoba == 0: continue
                result[team] = {
                    "xwoba":      xwoba,
                    "woba":       float(g(row,"woba") or 0),
                    "barrel_pct": float(g(row,"barrel_batted_rate","barrel_pct") or 0),
                    "avg_ev":     float(g(row,"exit_velocity_avg","avg_ev") or 0),
                    "pa":         int(float(g(row,"pa") or 0)),
                }
            if len(result) >= 25:
                print(f"  [✓] Savant xwOBA method {i}: {len(result)} teams")
                return result
            print(f"  [~] Savant method {i}: {len(result)} teams")
        except Exception as e:
            print(f"  [~] Savant method {i} failed: {e}")
    return {}


# ── Source 2: MLB Stats API — BB%, K%, traditional stats ─────────────────────
def fetch_mlb_hitting():
    url = (
        "https://statsapi.mlb.com/api/v1/teams/stats"
        f"?season={YEAR}&gameType=R&stats=season&group=hitting&sportId=1"
    )
    try:
        req  = urllib.request.Request(url, headers={"User-Agent":"mlb-fetch/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
        result = {}
        for split in data.get("stats",[{}])[0].get("splits",[]):
            tid  = split.get("team",{}).get("id")
            abbr = MLB_ID.get(tid)
            if not abbr: continue
            stat = split.get("stat",{})
            pa   = int(stat.get("plateAppearances",0) or 0)
            bb   = int(stat.get("baseOnBalls",0)      or 0)
            so   = int(stat.get("strikeOuts",0)       or 0)
            h    = int(stat.get("hits",0)             or 0)
            d2   = int(stat.get("doubles",0)          or 0)
            d3   = int(stat.get("triples",0)          or 0)
            hr   = int(stat.get("homeRuns",0)         or 0)
            ab   = int(stat.get("atBats",0)           or 0)
            sf   = int(stat.get("sacFlies",0)         or 0)
            ibb  = int(stat.get("intentionalWalks",0) or 0)
            hbp  = int(stat.get("hitByPitch",0)       or 0)
            s1b  = h - d2 - d3 - hr
            den  = ab + bb - ibb + sf + hbp
            woba = round((0.690*bb+0.722*hbp+0.884*s1b+1.261*d2+1.601*d3+2.072*hr)/den,3) \
                   if den > 0 else 0
            result[abbr] = {
                "pa":     pa,
                "bb_pct": round(bb / pa * 100, 2) if pa > 0 else 0.0,
                "k_pct":  round(so / pa * 100, 2) if pa > 0 else 0.0,
                "woba":   woba,
            }
        print(f"  [✓] MLB Stats API: {len(result)} teams (BB%, K%, wOBA)")
        return result
    except Exception as e:
        print(f"  [~] MLB Stats API failed: {e}")
        return {}


# ── Source 3: Aggregate HH% from statcast_batting.csv ────────────────────────
def aggregate_hh_from_statcast():
    """
    Read the already-downloaded individual batter Statcast CSV and
    aggregate hard_hit_percent and barrel_batted_rate to team level (PA-weighted).
    """
    if not SC_FILE.exists():
        print(f"  [~] {SC_FILE} not found — HH% unavailable")
        return {}

    totals = defaultdict(lambda: {"pa":0, "hh_sum":0.0, "barrel_sum":0.0})
    try:
        with open(SC_FILE, encoding="utf-8") as f:
            content = f.read().lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(content))
        cols   = [c.lower().strip() for c in (reader.fieldnames or [])]

        team_col = next((c for c in reader.fieldnames or []
                         if c.lower() in ("team_name","player_team","team")), None)
        for row in reader:
            raw  = row.get(team_col or "","").strip()
            team = SAVANT_NORM.get(raw) or SAVANT_NORM.get(raw.upper())
            if not team: continue
            try:
                pa  = int(float(row.get("pa","0") or 0))
                hh  = float(row.get("hard_hit_percent","0") or 0)
                bar = float(row.get("barrel_batted_rate","0") or 0)
                if pa < 10: continue
                t = totals[team]
                t["pa"]         += pa
                t["hh_sum"]     += hh  * pa
                t["barrel_sum"] += bar * pa
            except (ValueError, TypeError):
                continue

        result = {}
        for team, t in totals.items():
            if t["pa"] < 100: continue
            result[team] = {
                "hard_hit":   round(t["hh_sum"]     / t["pa"], 2),
                "barrel_pct": round(t["barrel_sum"] / t["pa"], 2),
            }
        n = len(result)
        if n >= 20:
            print(f"  [✓] HH% aggregated from statcast_batting.csv: {n} teams")
        else:
            print(f"  [~] Only {n} teams from statcast_batting.csv")
        return result
    except Exception as e:
        print(f"  [~] HH% aggregation failed: {e}")
        return {}


def main():
    print(f"Building team_batting.csv for {YEAR}...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    savant = fetch_savant_xwoba()      # xwOBA, wOBA, barrel, avg_ev
    mlb    = fetch_mlb_hitting()       # BB%, K%, wOBA (traditional)
    hh_map = aggregate_hh_from_statcast()  # HH%, barrel

    # Fall back to MLB API wOBA if Savant unavailable
    if not savant and mlb:
        print("  [~] Using MLB API as xwOBA proxy (wOBA)")
        savant = {t: {"xwoba": v["woba"], "woba": v["woba"],
                      "barrel_pct": 0.0, "avg_ev": 0.0, "pa": v["pa"]}
                  for t, v in mlb.items()}

    if not savant:
        print("  [✗] No team xwOBA data available")
        sys.exit(1)

    out = []
    all_teams = sorted(set(list(savant.keys()) + list(mlb.keys())))
    for team in all_teams:
        sv = savant.get(team, {})
        ml = mlb.get(team, {})
        hh = hh_map.get(team, {})

        xwoba     = sv.get("xwoba", ml.get("woba", LG_XWOBA))
        woba      = sv.get("woba",  ml.get("woba", xwoba))
        pa        = sv.get("pa",    ml.get("pa", 0))
        bb_pct    = ml.get("bb_pct",  0.0)
        k_pct     = ml.get("k_pct",   0.0)
        hard_hit  = hh.get("hard_hit",  sv.get("hard_hit",  0.0))
        barrel    = hh.get("barrel_pct",sv.get("barrel_pct",0.0))
        avg_ev    = sv.get("avg_ev", 0.0)

        off_score = calc_off_score(xwoba, bb_pct, hard_hit)
        wrc_plus  = calc_wrc_plus(woba or xwoba, team)

        out.append({
            "team":       team,
            "pa":         pa,
            "xwoba":      xwoba,
            "woba":       woba,
            "hard_hit":   hard_hit,
            "barrel_pct": barrel,
            "avg_ev":     avg_ev,
            "bb_pct":     bb_pct,
            "k_pct":      k_pct,
            "off_score":  off_score,
            "wrc_plus":   wrc_plus,
        })

    out.sort(key=lambda r: r["off_score"], reverse=True)

    with open(TEAM_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TEAM_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(out)

    print(f"\n  [✓] {TEAM_OUT} ({len(out)} teams)")
    print(f"\n  {'Team':<6} {'xwOBA':>6} {'BB%':>5} {'HH%':>5} {'off_score':>10} {'wRC+':>6}")
    print(f"  {'─'*42}")
    for r in out[:10]:
        print(f"  {r['team']:<6} {r['xwoba']:.3f}  {r['bb_pct']:>4.1f}  "
              f"{r['hard_hit']:>4.1f}  {r['off_score']:.4f}  {r['wrc_plus']}")


if __name__ == "__main__":
    main()
