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

TEAM_URL = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    "?year={year}"
    "&type=batter&group_by=team&filter=&min=0"
    "&selections=pa%2Cwoba%2Cxwoba%2Chard_hit_percent"
    "%2Cbarrel_batted_rate%2Cexit_velocity_avg"
    "&sort=xwoba&sortDir=desc&csv=true"
)

TEAM_OUT = OUT_DIR / "team_batting.csv"

# Baseball Savant team name → our standard abbreviation
SAVANT_NORM = {
    "Arizona Diamondbacks": "ARI",  "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",     "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",          "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",       "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",      "Detroit Tigers": "DET",
    "Houston Astros": "HOU",        "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA",    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",         "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",       "New York Mets": "NYM",
    "New York Yankees": "NYY",      "Oakland Athletics": "ATH",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SDP",      "San Francisco Giants": "SFG",
    "Seattle Mariners": "SEA",      "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR",        "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",     "Washington Nationals": "WSH",
    # short-form fallbacks
    "ARI":"ARI","ATL":"ATL","BAL":"BAL","BOS":"BOS","CHC":"CHC",
    "CWS":"CWS","CIN":"CIN","CLE":"CLE","COL":"COL","DET":"DET",
    "HOU":"HOU","KC":"KCR","KCR":"KCR","LAA":"LAA","LAD":"LAD",
    "MIA":"MIA","MIL":"MIL","MIN":"MIN","NYM":"NYM","NYY":"NYY",
    "OAK":"ATH","PHI":"PHI","PIT":"PIT","SD":"SDP","SDP":"SDP",
    "SF":"SFG","SFG":"SFG","SEA":"SEA","STL":"STL","TB":"TBR",
    "TBR":"TBR","TEX":"TEX","TOR":"TOR","WSH":"WSH","AZ":"ARI",
}


def fetch_team_batting(year: str = "2026") -> bool:
    """Fetch team-level batting xwOBA from Baseball Savant.

    Saves data/team_batting.csv with columns:
        team, pa, xwoba, woba, hard_hit, barrel_pct, avg_ev
    Returns True on success, False on failure.
    """
    url = TEAM_URL.format(year=year)
    print(f"\n  Fetching team batting ({year})...", end="", flush=True)
    try:
        content = fetch(url)
    except Exception as e:
        print(f" FAILED: {e}")
        return False

    reader = csv.DictReader(io.StringIO(content))
    cols   = reader.fieldnames or []

    # Detect team name column (varies by Savant response)
    team_col = next(
        (c for c in cols if c.lower() in ("team_name","team","abbreviation","player_team")),
        cols[0] if cols else ""
    )

    out_rows = []
    for row in reader:
        raw_team = row.get(team_col, "").strip()
        team = SAVANT_NORM.get(raw_team) or SAVANT_NORM.get(raw_team.upper())
        if not team:
            continue
        try:
            out_rows.append({
                "team":        team,
                "pa":          int(float(row.get("pa", 0) or 0)),
                "xwoba":       float(row.get("xwoba", 0) or 0),
                "woba":        float(row.get("woba", 0) or 0),
                "hard_hit":    float(row.get("hard_hit_percent", 0) or 0),
                "barrel_pct":  float(row.get("barrel_batted_rate", 0) or 0),
                "avg_ev":      float(row.get("exit_velocity_avg", 0) or 0),
            })
        except (ValueError, TypeError):
            continue

    if len(out_rows) < 20:
        print(f" only {len(out_rows)} teams — possible fetch issue, skipping write")
        return False

    # Sort by xwoba descending
    out_rows.sort(key=lambda r: r["xwoba"], reverse=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(TEAM_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["team","pa","xwoba","woba","hard_hit","barrel_pct","avg_ev"])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f" {len(out_rows)} teams → {TEAM_OUT}")
    for r in out_rows[:5]:
        print(f"    {r['team']:<5} xwOBA:{r['xwoba']:.3f}  HH%:{r['hard_hit']:.1f}  EV:{r['avg_ev']:.1f}")
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
