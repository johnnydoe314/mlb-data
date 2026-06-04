#!/usr/bin/env python3
"""
fetch_statcast_hitting.py
=========================
Downloads team-level Statcast hitting data from Baseball Savant
custom leaderboard (grouped by team).

Output: data/statcast_hitting_YEAR.csv

Matches the format of the existing statcast_hitting_2026.csv:
  Team, PA, BA, OBP, SLG, wOBA, wOBAcon, xBA, xSLG, xwOBA, xwOBAcon,
  Exit Velocity, Launch Angle, Sweet Spot%, Barrel%, Hard Hit%

Source: baseballsavant.mlb.com/leaderboard/custom (group_by=team)

Note on the league page (https://baseballsavant.mlb.com/league):
  That page renders data as images/charts with no direct CSV export.
  This script uses the custom leaderboard API instead, which provides
  the same underlying team Statcast data in CSV format.

Runs once daily at 10am CT via daily_data.yml.
"""

import csv, io, sys, time, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

OUT_DIR = Path("data")
YEAR    = 2026
TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://baseballsavant.mlb.com/",
    "DNT": "1",
}

# Team batting Statcast — custom leaderboard grouped by team
# Selections match the columns in statcast_hitting_2026.csv
TEAM_URL = (
    "https://baseballsavant.mlb.com/leaderboard/custom"
    "?year={year}"
    "&type=batter"
    "&filter=&min=q"
    "&selections="
    "pa,ab,hit,double,triple,home_run,walk,strikeout,"
    "batting_avg,on_base_percent,slg_percent,"
    "woba,woba_contact,"
    "xba,xslg,xwoba,xwoba_contact,"
    "exit_velocity_avg,launch_angle_avg,"
    "sweet_spot_percent,barrel_batted_rate,hard_hit_percent,"
    "pitches,batted_ball_events"
    "&group_by=team"
    "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm"
    "&sort=xwoba&sortDir=asc&csv=true"
)


def fetch(url: str, retries: int = 3) -> str:
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  [!] HTTP {e.code} attempt {attempt}/{retries}: {e.reason}")
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


def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    out_path = OUT_DIR / f"statcast_hitting_{YEAR}.csv"

    print("=" * 55)
    print(f"  FETCH STATCAST TEAM HITTING — {ts}")
    print(f"  Year: {YEAR}  |  Group: by team")
    print("=" * 55)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    url = TEAM_URL.format(year=YEAR)
    print(f"\n  Fetching team Statcast data...", end="", flush=True)

    try:
        content = fetch(url)
        lines   = content.strip().split("\n")
        rows    = len(lines) - 1

        print(f" {rows} teams")

        if rows < 20:
            print(f"  [!] Expected ~30 teams, got {rows} — check URL")
            print(f"  Headers: {lines[0][:120]}")
            sys.exit(1)

        # Add a Season column if not present
        hdrs = [h.strip() for h in lines[0].split(",")]
        if "year" in hdrs[0].lower() or "season" in hdrs[0].lower():
            # Already has year/season column
            pass
        else:
            # Inject a Season column
            lines[0] = f"Season,{lines[0]}"
            for i in range(1, len(lines)):
                if lines[i].strip():
                    lines[i] = f"{YEAR},{lines[i]}"
            content = "\n".join(lines)

        out_path.write_text(content, encoding="utf-8")
        size_kb = out_path.stat().st_size / 1024
        print(f"  [✓] {out_path}  ({size_kb:.0f} KB)")

        # Show column summary
        reader = csv.DictReader(io.StringIO(content))
        all_rows = list(reader)
        cols = list(all_rows[0].keys()) if all_rows else []
        print(f"  Columns ({len(cols)}): {', '.join(cols[:12])}...")
        print(f"\n  Sample (first 3 teams):")
        print(f"  {'Team':<6} {'xwOBA':>7} {'Barrel%':>8} {'HH%':>6} {'EV':>6}")
        print("  " + "─" * 34)
        for r in all_rows[:3]:
            team   = r.get("last_name, first_name", r.get("Team", r.get("team_id","?")))
            xwoba  = r.get("xwoba", r.get("xwOBA","?"))
            barrel = r.get("barrel_batted_rate", r.get("Barrel%","?"))
            hh     = r.get("hard_hit_percent", r.get("Hard Hit%","?"))
            ev     = r.get("exit_velocity_avg", r.get("Exit Velocity","?"))
            print(f"  {str(team):<6} {str(xwoba):>7} {str(barrel):>8} {str(hh):>6} {str(ev):>6}")

    except Exception as e:
        print(f"\n  [FAIL] {e}")
        sys.exit(1)

    print(f"\n  Done.")
    print("=" * 55)


if __name__ == "__main__":
    main()
