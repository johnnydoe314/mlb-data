#!/usr/bin/env python3
"""
fetch_statcast_hitting.py
=========================
Downloads individual batter Statcast data from Baseball Savant.
Multi-year (2026,2025,2024,2023), qualified PAs, sorted by xwOBA.

Output: data/statcast_batting.csv  (~1000+ rows, one per player)

NOTE: This is individual player data, NOT team aggregates.
      The team-level file (statcast_hitting_2026.csv) used by the
      composite model's batting edge is kept separate.

Source URL (user-specified):
  baseballsavant.mlb.com/leaderboard/custom?year=2026,2025,2024,2023
  &type=batter&group_by=name (default — individual players)
  &selections=player_age,ab,pa,hit,...,whiff_percent,swing_percent

Runs daily at 10am CT via daily_data.yml.
"""

import csv, io, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR  = Path("data")
OUT_FILE = OUT_DIR / "statcast_batting.csv"
TIMEOUT  = 30

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

# Exact URL provided — csv=true appended to trigger download
URL = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    "?year=2026%2C2025%2C2024%2C2023"
    "&type=batter"
    "&filter="
    "&min=q"
    "&selections=player_age%2Cab%2Cpa%2Chit%2Csingle%2Cdouble%2Ctriple%2Chome_run"
    "%2Cstrikeout%2Cwalk%2Ck_percent%2Cbb_percent%2Cbatting_avg%2Cslg_percent"
    "%2Con_base_percent%2Con_base_plus_slg%2Cxba%2Cxslg%2Cwoba%2Cxwoba"
    "%2Cxobp%2Cxiso%2Cavg_swing_speed%2Cfast_swing_rate%2Cblasts_contact"
    "%2Cblasts_swing%2Csquared_up_contact%2Csquared_up_swing%2Cavg_swing_length"
    "%2Cswords%2Cattack_angle%2Cattack_direction%2Cideal_angle_rate"
    "%2Cvertical_swing_path%2Cexit_velocity_avg%2Claunch_angle_avg"
    "%2Csweet_spot_percent%2Cbarrel_batted_rate%2Chard_hit_percent"
    "%2Cavg_best_speed%2Cavg_hyper_speed%2Cwhiff_percent%2Cswing_percent"
    "&chart=false"
    "&x=player_age&y=player_age&r=no&chartType=beeswarm"
    "&sort=xwoba&sortDir=desc"
    "&csv=true"   # required for CSV download
)


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} attempt {attempt}/{retries}")
            if attempt < retries:
                time.sleep(attempt * 5)
            else:
                raise
        except Exception as e:
            print(f"  [!] Error attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(5)
            else:
                raise



# ── Team batting fetch ────────────────────────────────────────────────────────

# Baseball Savant team name → standard abbreviation
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

TEAM_OUT    = OUT_DIR / "team_batting.csv"
TEAM_FIELDS = ["team","pa","xwoba","woba","hard_hit","barrel_pct","avg_ev"]

# MLB team ID → our abbreviation (for API fallback)
MLB_ID_TO_ABBR = {
    109:"ARI",144:"ATL",110:"BAL",111:"BOS",112:"CHC",145:"CWS",
    113:"CIN",114:"CLE",115:"COL",116:"DET",117:"HOU",118:"KCR",
    108:"LAA",119:"LAD",146:"MIA",158:"MIL",142:"MIN",121:"NYM",
    147:"NYY",133:"ATH",143:"PHI",134:"PIT",135:"SDP",137:"SFG",
    136:"SEA",138:"STL",139:"TBR",140:"TEX",141:"TOR",120:"WSH",
}


def _parse_savant_team_csv(content: str) -> list:
    """Parse Baseball Savant team CSV, normalize team names."""
    import csv as _csv, io as _io
    reader = _csv.DictReader(_io.StringIO(content))
    cols   = reader.fieldnames or []
    team_col = next(
        (c for c in cols if c.lower() in
         ("team_name","team","abbreviation","player_team","club")),
        cols[0] if cols else ""
    )
    rows = []
    for row in reader:
        raw  = row.get(team_col,"").strip()
        team = SAVANT_NORM.get(raw) or SAVANT_NORM.get(raw.upper())
        if not team: continue
        try:
            rows.append({
                "team":       team,
                "pa":         int(float(row.get("pa",0) or 0)),
                "xwoba":      float(row.get("xwoba",0) or 0),
                "woba":       float(row.get("woba",0)  or 0),
                "hard_hit":   float(row.get("hard_hit_percent",0) or 0),
                "barrel_pct": float(row.get("barrel_batted_rate",0) or 0),
                "avg_ev":     float(row.get("exit_velocity_avg",0) or 0),
            })
        except (ValueError, TypeError): continue
    return rows


def _mlb_api_fallback(year: str = "2026") -> list:
    """Fallback: MLB Stats API + compute wOBA from traditional components.

    wOBA formula (2026 weights):
      (0.690×BB + 0.722×HBP + 0.884×1B + 1.261×2B + 1.601×3B + 2.072×HR)
      ────────────────────────────────────────────────────────────────────
      (AB + BB − IBB + SF + HBP)
    """
    import json as _json

    def _woba(s):
        bb   = int(s.get("baseOnBalls",0)       or 0)
        hbp  = int(s.get("hitByPitch",0)        or 0)
        h    = int(s.get("hits",0)              or 0)
        d2   = int(s.get("doubles",0)           or 0)
        d3   = int(s.get("triples",0)           or 0)
        hr   = int(s.get("homeRuns",0)          or 0)
        ab   = int(s.get("atBats",0)            or 0)
        sf   = int(s.get("sacFlies",0)          or 0)
        ibb  = int(s.get("intentionalWalks",0)  or 0)
        s1b  = h - d2 - d3 - hr
        num  = (0.690*bb + 0.722*hbp + 0.884*s1b +
                1.261*d2  + 1.601*d3  + 2.072*hr)
        den  = ab + bb - ibb + sf + hbp
        return round(num / den, 3) if den > 0 else 0.0

    url = (
        f"https://statsapi.mlb.com/api/v1/teams/stats"
        f"?season={year}&gameType=R&stats=season&group=hitting&sportId=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent":"mlb-fetch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = _json.loads(r.read())
    rows = []
    for split in data.get("stats",[{}])[0].get("splits",[]):
        tid  = split.get("team",{}).get("id")
        abbr = MLB_ID_TO_ABBR.get(tid)
        if not abbr: continue
        stat = split.get("stat",{})
        try:
            woba = _woba(stat)
            rows.append({
                "team":       abbr,
                "pa":         int(stat.get("plateAppearances",0) or 0),
                "xwoba":      woba,   # computed wOBA as proxy for xwOBA
                "woba":       woba,
                "hard_hit":   0.0,
                "barrel_pct": 0.0,
                "avg_ev":     0.0,
            })
        except (ValueError, TypeError): continue
    return rows


def fetch_team_batting(year: str = "2026") -> bool:
    """Fetch team-level batting xwOBA. Tries three sources in order:
      1. Baseball Savant /leaderboard/custom?type=batter with team aggregation
      2. Baseball Savant statcast_search CSV with group_by=team
      3. MLB Stats API (wOBA proxy — no xwOBA)
    Writes data/team_batting.csv on success.
    """
    import csv as _csv

    URLS = [
        # Method 1: Savant custom leaderboard — type=batter, team grouping
        (
            "https://baseballsavant.mlb.com/leaderboard/custom"
            f"?year={year}&type=batter&group_by=team&filter=&min=0"
            "&selections=pa%2Cwoba%2Cxwoba%2Chard_hit_percent"
            "%2Cbarrel_batted_rate%2Cexit_velocity_avg"
            "&sort=xwoba&sortDir=desc&csv=true"
        ),
        # Method 2: Savant statcast_search CSV
        (
            "https://baseballsavant.mlb.com/statcast_search/csv"
            f"?all=true&hfGT=R%7C&hfSea={year}%7C"
            "&player_type=batter&group_by=team"
            "&sort_col=xwoba&sort_order=desc&min_results=0"
        ),
    ]

    print(f"\n  Fetching team batting ({year})...", flush=True)

    out_rows = []
    for i, url in enumerate(URLS, 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                content = r.read().decode("utf-8")
            rows = _parse_savant_team_csv(content)
            if len(rows) >= 25:
                out_rows = rows
                print(f"  [✓] Savant method {i}: {len(rows)} teams")
                break
            print(f"  [~] Savant method {i}: only {len(rows)} teams, trying next")
        except Exception as e:
            print(f"  [~] Savant method {i} failed: {e}")

    if not out_rows:
        print("  [~] Savant unavailable — falling back to MLB Stats API")
        try:
            out_rows = _mlb_api_fallback(year)
            if out_rows:
                print(f"  [✓] MLB API fallback: {len(out_rows)} teams (wOBA proxy, no xwOBA)")
        except Exception as e:
            print(f"  [✗] MLB API fallback failed: {e}")

    if not out_rows:
        print("  [✗] All sources failed — team_batting.csv not written")
        return False

    out_rows.sort(key=lambda r: r["xwoba"], reverse=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(TEAM_OUT, "w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=TEAM_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"  [✓] {TEAM_OUT}  ({len(out_rows)} teams)")
    for r in out_rows[:5]:
        print(f"      {r['team']:<5} xwOBA:{r['xwoba']:.3f}  HH%:{r['hard_hit']:.1f}")
    return True

def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    print("=" * 58)
    print(f"  FETCH STATCAST BATTER DATA — {ts}")
    print(f"  Years: 2026,2025,2024,2023  |  Level: individual player")
    print("=" * 58)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n  Fetching...", end="", flush=True)

    try:
        content = fetch(URL)
    except Exception as e:
        print(f"\n  [FAIL] {e}")
        sys.exit(1)

    lines = [ln for ln in content.strip().split("\n") if ln.strip()]
    rows  = len(lines) - 1  # subtract header

    print(f" {rows} players")

    if rows < 100:
        print(f"  [!] Expected 1000+ rows, got {rows} — possible fetch issue")
        print(f"  Preview: {lines[0][:120] if lines else 'empty'}")
        sys.exit(1)

    OUT_FILE.write_text(content, encoding="utf-8")
    size_kb = OUT_FILE.stat().st_size / 1024
    print(f"  [✓] {OUT_FILE}  ({rows} players, {size_kb:.0f} KB)")

    # Show column list
    reader = csv.DictReader(io.StringIO(content))
    cols   = reader.fieldnames or []
    print(f"\n  Columns ({len(cols)}):")
    for i in range(0, len(cols), 6):
        print(f"    {', '.join(cols[i:i+6])}")

    # Top 5 by xwOBA as a sanity check
    rows_data = list(reader)
    xwoba_col = next((c for c in cols if c.lower() == 'xwoba'), '')
    name_col  = next((c for c in cols if 'last_name' in c.lower()), cols[0] if cols else '')
    if xwoba_col and rows_data:
        print(f"\n  Top 5 by xwOBA:")
        for r in rows_data[:5]:
            print(f"    {r.get(name_col,'?'):<25} xwOBA:{r.get(xwoba_col,'?')}")

    # Also fetch team-level batting aggregates
    fetch_team_batting(year="2026")

    print(f"\n  Done.")
    print("=" * 58)


if __name__ == "__main__":
    main()
