#!/usr/bin/env python3
"""
fetch_statcast_hitting.py
=========================
Downloads team-level Statcast batting data from Baseball Savant.
Computes off_score composite (xwOBA + BB% + Hard Hit%) for each team.

Outputs:
  data/team_batting.csv  — team offensive stats + off_score
"""

import csv, io, json, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR   = Path("data")
TEAM_OUT  = OUT_DIR / "team_batting.csv"
TIMEOUT   = 30
YEAR      = "2026"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
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

MLB_ID_TO_ABBR = {
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

# ── OFF_SCORE formula ─────────────────────────────────────────────────────────
LG_XWOBA = 0.318; LG_BB = 8.5; LG_HH = 37.0
WOBA_SCALE = 1.24; LG_R_PA = 0.119
PARK_ADJ = {
    "COL":1.10,"BOS":1.05,"NYY":1.05,"CHC":1.03,"CIN":1.03,"TEX":1.01,
    "HOU":1.01,"SDP":0.97,"SFG":0.97,"TBR":0.99,"TOR":1.01,"MIN":1.01,
}

def calc_off_score(xwoba, bb_pct, hard_hit_pct):
    bb_norm = LG_XWOBA + (bb_pct - LG_BB) * 0.006
    hh_norm = LG_XWOBA + (hard_hit_pct - LG_HH) * 0.003
    return round(xwoba * 0.55 + bb_norm * 0.25 + hh_norm * 0.20, 4)

def calc_wrc_plus(woba, team):
    pf  = PARK_ADJ.get(team, 1.00)
    rate = (woba - LG_XWOBA) / WOBA_SCALE + LG_R_PA
    return round((rate / LG_R_PA) * 100 / pf)


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

def _parse_savant(content):
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    cols   = [c.lower() for c in (reader.fieldnames or [])]
    orig   = reader.fieldnames or []
    col_map = {c.lower(): c for c in orig}

    def g(row, *names):
        for n in names:
            v = row.get(col_map.get(n,""), "")
            if v not in ("",None): return v
        return "0"

    team_key = next((col_map.get(c) for c in
        ["team_name","abbreviation","team","club","player_team"] if col_map.get(c)), None)

    rows = []
    for row in reader:
        raw  = row.get(team_key or "","").strip()
        team = SAVANT_NORM.get(raw) or SAVANT_NORM.get(raw.upper())
        if not team: continue
        try:
            xwoba    = float(g(row,"xwoba","xwoba_","expected_woba") or 0)
            woba     = float(g(row,"woba","woba_","weighted_on_base_average") or 0)
            hard_hit = float(g(row,"hard_hit_percent","hardhit_percent","hard_hit") or 0)
            barrel   = float(g(row,"barrel_batted_rate","barrel_pct","barrel") or 0)
            avg_ev   = float(g(row,"exit_velocity_avg","avg_ev","exit_velocity") or 0)
            bb_pct   = float(g(row,"bb_percent","bb_pct","walk_percent","bb%") or 0)
            k_pct    = float(g(row,"k_percent","k_pct","strikeout_percent","k%") or 0)
            pa       = int(float(g(row,"pa","plate_appearances") or 0))
            if xwoba == 0: continue
            rows.append({"team":team,"pa":pa,"xwoba":xwoba,"woba":woba,
                         "hard_hit":hard_hit,"barrel_pct":barrel,"avg_ev":avg_ev,
                         "bb_pct":bb_pct,"k_pct":k_pct})
        except (ValueError, TypeError): continue
    return rows

def _mlb_api_fallback():
    url = (
        "https://statsapi.mlb.com/api/v1/teams/stats"
        f"?season={YEAR}&gameType=R&stats=season&group=hitting&sportId=1"
    )
    req  = urllib.request.Request(url, headers={"User-Agent":"mlb-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read())
    rows = []
    for split in data.get("stats",[{}])[0].get("splits",[]):
        tid  = split.get("team",{}).get("id")
        abbr = MLB_ID_TO_ABBR.get(tid)
        if not abbr: continue
        stat = split.get("stat",{})
        bb   = int(stat.get("baseOnBalls",0)      or 0)
        hbp  = int(stat.get("hitByPitch",0)       or 0)
        h    = int(stat.get("hits",0)             or 0)
        d2   = int(stat.get("doubles",0)          or 0)
        d3   = int(stat.get("triples",0)          or 0)
        hr   = int(stat.get("homeRuns",0)         or 0)
        ab   = int(stat.get("atBats",0)           or 0)
        sf   = int(stat.get("sacFlies",0)         or 0)
        ibb  = int(stat.get("intentionalWalks",0) or 0)
        pa   = int(stat.get("plateAppearances",0) or 0)
        s1b  = h - d2 - d3 - hr
        den  = ab + bb - ibb + sf + hbp
        woba = round((0.690*bb+0.722*hbp+0.884*s1b+1.261*d2+1.601*d3+2.072*hr)/den,3) if den>0 else 0
        bb_pct = round(bb/pa*100,1) if pa>0 else 0
        rows.append({"team":abbr,"pa":pa,"xwoba":woba,"woba":woba,
                     "hard_hit":0.0,"barrel_pct":0.0,"avg_ev":0.0,
                     "bb_pct":bb_pct,"k_pct":0.0})
    return rows


def main():
    print(f"Fetching team batting {YEAR}...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    URLS = [
        # Group-by-team with all needed columns
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={YEAR}&type=batter&group_by=team&filter=&min=0"
            "&selections=pa%2Cwoba%2Cxwoba%2Chard_hit_percent"
            "%2Cbarrel_batted_rate%2Cexit_velocity_avg%2Cbb_percent%2Ck_percent"
            "&sort=xwoba&sortDir=desc&csv=true"
        ),
        # Statcast search CSV group_by=team
        (
            "https://baseballsavant.mlb.com/statcast_search/csv"
            f"?all=true&hfGT=R%7C&hfSea={YEAR}%7C"
            "&player_type=batter&group_by=team&min_results=0"
        ),
    ]

    rows = []
    for i, url in enumerate(URLS, 1):
        try:
            content = _fetch(url)
            rows = _parse_savant(content)
            if len(rows) >= 25:
                print(f"  [✓] Savant method {i}: {len(rows)} teams")
                break
            print(f"  [~] Method {i}: {len(rows)} teams — trying next")
        except Exception as e:
            print(f"  [~] Method {i} failed: {e}")

    if not rows:
        print("  [~] Savant unavailable — MLB Stats API fallback")
        try:
            rows = _mlb_api_fallback()
            print(f"  [✓] API fallback: {len(rows)} teams (wOBA proxy)")
        except Exception as e:
            print(f"  [✗] All sources failed: {e}")
            sys.exit(1)

    # Compute off_score + wrc_plus for each team
    out = []
    for r in sorted(rows, key=lambda x: x["xwoba"], reverse=True):
        r["off_score"] = calc_off_score(r["xwoba"], r["bb_pct"], r["hard_hit"])
        r["wrc_plus"]  = calc_wrc_plus(r["woba"] or r["xwoba"], r["team"])
        out.append(r)

    with open(TEAM_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TEAM_FIELDS, extrasaction="ignore")
        writer.writeheader(); writer.writerows(out)

    print(f"  [✓] {TEAM_OUT} ({len(out)} teams)")
    for r in out[:5]:
        print(f"    {r['team']:<5} xwOBA:{r['xwoba']:.3f}  BB%:{r['bb_pct']:.1f}"
              f"  HH%:{r['hard_hit']:.1f}  off_score:{r['off_score']:.4f}  wRC+:{r['wrc_plus']}")


if __name__ == "__main__":
    main()
