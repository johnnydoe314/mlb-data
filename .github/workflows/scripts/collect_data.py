"""
MLB Daily Data Collector
========================
Runs via GitHub Actions daily at 10am CT.
Collects and commits:
  1. Probable starters (MLB Stats API)
  2. Pitcher Statcast data (Baseball Savant)
  3. Team batting Statcast data (Baseball Savant)
  4. Pitcher vs batter matchup data (Baseball Reference via pybaseball)

All outputs go to /data/ as CSVs readable by run_analysis.py
and fetchable from raw.githubusercontent.com.
"""

import requests
import pandas as pd
import json
import csv
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

YEAR = date.today().year
TODAY = date.today().strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------------------------
# 1 — Probable starters from MLB Stats API
# ---------------------------------------------------------------------------

def fetch_probable_pitchers(game_date: str = TODAY) -> list[dict]:
    """Pull today's probable pitchers from the official MLB Stats API."""
    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={game_date}"
        f"&hydrate=probablePitcher(note),team,venue"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  [!] MLB Stats API error: {e}")
        return []

    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_p = away.get("probablePitcher", {})
            home_p = home.get("probablePitcher", {})
            status = g.get("status", {}).get("detailedState", "")

            games.append({
                "game_date":       game_date,
                "game_pk":         g.get("gamePk"),
                "game_time":       g.get("gameDate", ""),
                "venue":           g.get("venue", {}).get("name", ""),
                "away_team":       away["team"].get("abbreviation", ""),
                "away_team_name":  away["team"].get("name", ""),
                "home_team":       home["team"].get("abbreviation", ""),
                "home_team_name":  home["team"].get("name", ""),
                "away_pitcher_id": away_p.get("id", ""),
                "away_pitcher":    away_p.get("fullName", "TBD"),
                "home_pitcher_id": home_p.get("id", ""),
                "home_pitcher":    home_p.get("fullName", "TBD"),
                "status":          status,
            })

    print(f"  → Probable pitchers: {len(games)} games found for {game_date}")
    return games


def save_probable_pitchers(games: list[dict]):
    path = DATA_DIR / "probable_pitchers.csv"
    if not games:
        return
    fields = list(games[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(games)
    print(f"  [✓] Saved → {path}")
    return path


# ---------------------------------------------------------------------------
# 2 — Pitcher Statcast leaderboard (Baseball Savant)
# ---------------------------------------------------------------------------

def fetch_pitcher_statcast(year: int = YEAR) -> pd.DataFrame:
    """
    Pull pitcher-level Statcast metrics from Baseball Savant.
    Uses the exact same URL/parameters as the manual Download CSV button.
    Multi-year (2023-2026) so the file stays comprehensive across rosters.
    """
    # Matches: baseballsavant.mlb.com/leaderboard/custom?year=2026,2025,2024,2023
    # &type=pitcher&min=50&selections=pa,k_percent,...&csv=true
    years = "%2C".join(str(y) for y in range(year, year-4, -1))
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={years}"
        "&type=pitcher&filter=&min=50"
        "&selections=pa,k_percent,bb_percent,woba,xwoba,"
        "sweet_spot_percent,barrel_batted_rate,hard_hit_percent,"
        "avg_best_speed,avg_hyper_speed,whiff_percent,swing_percent"
        "&chart=false&x=pa&y=pa&r=no&chartType=beeswarm"
        "&sort=xwoba&sortDir=asc&csv=true"
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        print(f"  → Pitcher Statcast: {len(df)} pitchers")
        return df
    except Exception as e:
        print(f"  [!] Pitcher Statcast error: {e}")
        return pd.DataFrame()


def save_pitcher_statcast(df: pd.DataFrame):
    if df.empty:
        return
    path = DATA_DIR / "stats.csv"
    df.to_csv(path, index=False)
    print(f"  [✓] Saved → {path}")
    return path


# ---------------------------------------------------------------------------
# 3 — Team batting Statcast (Baseball Savant)
# ---------------------------------------------------------------------------

def fetch_team_batting(year: int = YEAR) -> pd.DataFrame:
    """
    Pull team-level batting Statcast metrics.
    This gives us the xwOBA, hard_hit%, barrel% used in our composite model.
    """
    # Use the sprint speed / exit velocity leaderboard at team level
    url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={year}&type=batter&filter=&sort=2&sortDir=asc"
        "&min=1&selections=pa,ab,hit,home_run,k_percent,bb_percent,"
        "woba,xwoba,sweet_spot_percent,barrel_batted_rate,hard_hit_percent,"
        "avg_best_speed,avg_hyper_speed,whiff_percent,swing_percent"
        "&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm"
        "&csv=true&team=1"  # team=1 aggregates by team
    )
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        print(f"  → Team batting Statcast: {len(df)} teams")
        return df
    except Exception as e:
        print(f"  [!] Team batting error: {e}. Trying alternate endpoint...")
        return fetch_team_batting_alternate(year)


def fetch_team_batting_alternate(year: int = YEAR) -> pd.DataFrame:
    """Fallback: use pybaseball to fetch team batting data."""
    try:
        from pybaseball import team_batting
        df = team_batting(year)
        print(f"  → Team batting (pybaseball fallback): {len(df)} rows")
        return df
    except Exception as e:
        print(f"  [!] Pybaseball team batting error: {e}")
        return pd.DataFrame()


def save_team_batting(df: pd.DataFrame):
    if df.empty:
        return
    path = DATA_DIR / "statcast_hitting.csv"
    df.to_csv(path, index=False)
    print(f"  [✓] Saved → {path}")
    return path


# ---------------------------------------------------------------------------
# 4 — Pitcher vs batter matchup data (Baseball Reference via pybaseball)
# ---------------------------------------------------------------------------

def fetch_matchup_data(pitcher_ids: list[str]) -> pd.DataFrame:
    """
    Attempt to pull pitcher-vs-batter matchup data.
    Baseball Reference blocks direct scraping but pybaseball has a cache.
    Falls back gracefully if unavailable.
    """
    if not pitcher_ids:
        print("  [!] No pitcher IDs provided for matchup data")
        return pd.DataFrame()

    try:
        from pybaseball import split_stats
        all_splits = []
        for pid in pitcher_ids[:20]:  # limit to avoid rate limiting
            try:
                df = split_stats(pid, "pitching", YEAR)
                if not df.empty:
                    df["pitcher_id"] = pid
                    all_splits.append(df)
                time.sleep(1)  # respectful rate limiting
            except Exception:
                continue

        if all_splits:
            result = pd.concat(all_splits, ignore_index=True)
            print(f"  → Matchup splits: {len(result)} rows")
            return result
        return pd.DataFrame()
    except Exception as e:
        print(f"  [!] Matchup data unavailable: {e}")
        return pd.DataFrame()


def fetch_probable_pitcher_ids(games: list[dict]) -> list[str]:
    """Extract MLB pitcher IDs from probable pitcher game list."""
    ids = []
    for g in games:
        if g.get("away_pitcher_id"):
            ids.append(str(g["away_pitcher_id"]))
        if g.get("home_pitcher_id"):
            ids.append(str(g["home_pitcher_id"]))
    return [i for i in ids if i and i != "None"]


# ---------------------------------------------------------------------------
# 5 — Write metadata file
# ---------------------------------------------------------------------------

def write_metadata(games: list[dict], stats_ok: bool, batting_ok: bool):
    meta = {
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "game_date": TODAY,
        "games_found": len(games),
        "pitcher_statcast": "ok" if stats_ok else "failed",
        "team_batting": "ok" if batting_ok else "failed",
        "pitchers": [
            {
                "game": f"{g['away_team']} @ {g['home_team']}",
                "away_pitcher": g["away_pitcher"],
                "home_pitcher": g["home_pitcher"],
            }
            for g in games
        ]
    }
    path = DATA_DIR / "metadata.json"
    with open(path, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"  [✓] Metadata → {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print(f"  MLB DATA COLLECTOR — {TODAY}")
    print("=" * 60)

    print("\n[1/4] Fetching probable pitchers...")
    games = fetch_probable_pitchers(TODAY)
    save_probable_pitchers(games)

    print("\n[2/4] Fetching pitcher Statcast data...")
    pitcher_df = fetch_pitcher_statcast(YEAR)
    stats_ok = not pitcher_df.empty
    save_pitcher_statcast(pitcher_df)

    print("\n[3/4] Fetching team batting Statcast data...")
    batting_df = fetch_team_batting(YEAR)
    batting_ok = not batting_df.empty
    save_team_batting(batting_df)

    print("\n[4/4] Writing metadata...")
    write_metadata(games, stats_ok, batting_ok)

    print("\n" + "=" * 60)
    print(f"  Done. Data in /data/ — {len(games)} games found.")
    print("=" * 60)


if __name__ == "__main__":
    main()
