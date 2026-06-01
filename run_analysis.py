#!/usr/bin/env python3
"""
MLB Daily Analysis — with GitHub auto-fetch
============================================
Extends the original run_analysis.py to automatically pull fresh data
from your GitHub repo when local files are missing or stale (>12 hours old).

GitHub repo format:  https://github.com/YOUR_USERNAME/YOUR_REPO
Set GITHUB_RAW_BASE in the config block below.

Usage:
    python run_analysis.py                    # full run, auto-fetch if stale
    python run_analysis.py --no-fetch         # skip GitHub fetch, use local only
    python run_analysis.py --fetch-only       # fetch and exit
    python run_analysis.py --auto             # skip validation gate
    python run_analysis.py --composite-only   # run composite model only
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing: pip install beautifulsoup4")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — set your GitHub repo here
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/johnnydoe314/mlb-data/main/data"
)

# Remote file names (must match what collect_data.py writes)
REMOTE_FILES = {
    "probable_pitchers": "probable_pitchers.csv",
    "stats":             "stats.csv",
    "statcast_hitting":  "statcast_hitting.csv",
    "metadata":          "metadata.json",
}

# Local paths
LOCAL_DIR       = Path(".")
STATCAST_PATH   = LOCAL_DIR / "statcast_hitting_2026.csv"
STATS_PATH      = LOCAL_DIR / "stats.csv"
XLS_PATH        = LOCAL_DIR / "sportsref_download.xls"
OUTPUT_DIR      = Path("analysis_output")
MIN_PA          = 15
STALE_HOURS     = 12   # re-fetch if file is older than this

PARK_ADJ = {
    "COL": -3.0, "BOS": -1.5, "NYY": -1.5, "CHC": -1.0, "CIN": -1.0,
    "TEX": -0.5, "HOU": -0.5, "SDP": +1.5, "SFG": +1.5, "TBR": +0.5,
    "TOR": +0.5, "MIN": +0.5,
}

# ─────────────────────────────────────────────────────────────────────────────
# Auto-fetch from GitHub
# ─────────────────────────────────────────────────────────────────────────────

def is_stale(path: Path, hours: int = STALE_HOURS) -> bool:
    """Return True if file doesn't exist or is older than `hours`."""
    if not path.exists():
        return True
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    return age > timedelta(hours=hours)


def fetch_remote(filename: str, local_path: Path) -> bool:
    """Download a file from the GitHub raw URL and save locally."""
    url = f"{GITHUB_RAW_BASE}/{filename}"
    try:
        print(f"  → Fetching {url} ...", file=sys.stderr)
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "run_analysis/1.0"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            content = r.read()
        with open(local_path, "wb") as f:
            f.write(content)
        print(f"  [✓] Saved → {local_path}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"  [!] Fetch failed ({url}): {e}", file=sys.stderr)
        return False


def auto_fetch(force: bool = False):
    """
    Fetch remote data files if local copies are missing or stale.
    Returns True if at least the probable pitchers were fetched.
    """
    print("[AUTO-FETCH] Checking data freshness...", file=sys.stderr)

    fetched_any = False

    # Check metadata first to see when remote was last updated
    meta_path = OUTPUT_DIR / "remote_metadata.json"
    if force or is_stale(meta_path, hours=1):
        if fetch_remote(REMOTE_FILES["metadata"], meta_path):
            try:
                meta = json.loads(meta_path.read_text())
                updated = meta.get("last_updated", "unknown")
                games = meta.get("games_found", 0)
                print(f"  Remote data: {games} games, updated {updated}",
                      file=sys.stderr)
            except Exception:
                pass

    # Fetch probable pitchers if stale
    sp_path = OUTPUT_DIR / "probable_pitchers_remote.csv"
    if force or is_stale(sp_path, hours=2):
        if fetch_remote(REMOTE_FILES["probable_pitchers"], sp_path):
            fetched_any = True

    # Fetch pitcher Statcast if stale (refreshes every few days)
    if force or is_stale(STATS_PATH, hours=48):
        fetch_remote(REMOTE_FILES["stats"], STATS_PATH)

    # Fetch team batting if stale
    if force or is_stale(STATCAST_PATH, hours=48):
        fetch_remote(REMOTE_FILES["statcast_hitting"], STATCAST_PATH)

    return fetched_any, sp_path if sp_path.exists() else None


# ─────────────────────────────────────────────────────────────────────────────
# Composite model
# ─────────────────────────────────────────────────────────────────────────────

def load_pitcher_statcast(path: Path) -> dict:
    """Load pitcher stats keyed by 'Last, First' name."""
    pitchers = {}
    if not path.exists():
        return pitchers
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row.get("last_name, first_name", "").strip()
            if not name:
                continue
            try:
                pitchers[name] = {
                    "woba":       float(row.get("woba", 0) or 0),
                    "xwoba":      float(row.get("xwoba", 0) or 0),
                    "gap":        round(
                        float(row.get("woba", 0) or 0) -
                        float(row.get("xwoba", 0) or 0), 3
                    ),
                    "hard_hit":   float(row.get("hard_hit_percent", 0) or 0),
                    "whiff":      float(row.get("whiff_percent", 0) or 0),
                    "k_pct":      float(row.get("k_percent", 0) or 0),
                }
            except (ValueError, KeyError):
                continue
    return pitchers


def load_team_batting(path: Path) -> dict:
    """Load team batting keyed by team abbreviation."""
    teams = {}
    if not path.exists():
        return teams
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            team = row.get("Team", "").strip().upper()
            if not team or team == "MLB":
                continue
            pa = int(row.get("PA", 0) or 0)
            if team in teams and pa <= teams[team]["pa"]:
                continue
            try:
                teams[team] = {
                    "pa":        pa,
                    "xwoba":     float(row.get("xwOBA", 0) or 0),
                    "hard_hit":  float(row.get("Hard Hit%", 0) or 0),
                    "barrel":    float(row.get("Barrel%", 0) or 0),
                    "exit_velo": float(row.get("Exit Velocity", 0) or 0),
                }
            except (ValueError, KeyError):
                continue
    return teams


def compute_composite(away_sp: str, home_sp: str,
                       away_team: str, home_team: str,
                       pitchers: dict, teams: dict) -> dict:
    """
    Compute composite score for a matchup.
    Positive composite = model favors AWAY.
    Negative composite = model favors HOME.
    """
    a = pitchers.get(away_sp)
    h = pitchers.get(home_sp)
    ab = teams.get(away_team)
    hb = teams.get(home_team)

    sp_edge = 0.0
    if a:
        sp_edge += (a["gap"] * -100)
    if h:
        sp_edge -= (h["gap"] * -100)

    bat_edge = 0.0
    if ab and hb:
        bat_edge = (ab["xwoba"] - hb["xwoba"]) * 100

    raw = round(sp_edge + bat_edge, 2)
    park = PARK_ADJ.get(home_team, 0.0)
    adj = round(raw + (park if raw > 0 else -park if raw < 0 else 0), 2)
    abs_adj = abs(adj)

    band = (
        "8+" if abs_adj >= 8 else
        "5-8" if abs_adj >= 5 else
        "2-5" if abs_adj >= 2 else
        "0-2"
    )
    model = "AWAY" if adj > 2 else ("HOME" if adj < -2 else "NEUT")

    # Bilateral alignment (standard: both > 1.5, or SP-dominant: |SP|>=3, |bat|<=1.5)
    std_bilateral = (sp_edge > 1.5 and bat_edge > 1.5) or \
                    (sp_edge < -1.5 and bat_edge < -1.5)
    sp_dominant   = abs(sp_edge) >= 3.0 and abs(bat_edge) <= 1.5
    aligned = std_bilateral or (sp_dominant and abs_adj >= 5)

    missing = (away_sp and not a) or (home_sp and not h)

    qualifies = abs_adj >= 5 and aligned and not missing

    return {
        "sp_edge":   sp_edge,
        "bat_edge":  bat_edge,
        "park_adj":  park,
        "raw":       raw,
        "composite": adj,
        "abs":       abs_adj,
        "band":      band,
        "model":     model,
        "aligned":   aligned,
        "missing_sp": missing,
        "qualifies": qualifies,
        "away_sp_data": a,
        "home_sp_data": h,
        "away_bat_data": ab,
        "home_bat_data": hb,
    }


def print_composite_report(games: list[dict], pitchers: dict, teams: dict):
    """Print the composite model report for all games."""
    BOLD  = "\033[1m"
    GREEN = "\033[92m"
    AMBER = "\033[93m"
    RED   = "\033[91m"
    DIM   = "\033[2m"
    RESET = "\033[0m"

    def c(text, *codes):
        if not sys.stdout.isatty():
            return text
        return "".join(codes) + text + RESET

    print()
    print(c("═" * 80, BOLD))
    print(c("  COMPOSITE MODEL — TODAY'S SLATE", BOLD))
    print(c(f"  {datetime.now().strftime('%A %B %d, %Y')}  |  {len(games)} games", DIM))
    print(c("═" * 80, BOLD))
    print()

    qualifying = []

    for g in games:
        away = g.get("away_team", "?")
        home = g.get("home_team", "?")
        away_sp = g.get("away_pitcher", "TBD")
        home_sp = g.get("home_pitcher", "TBD")
        game_time = g.get("game_time", "")

        r = compute_composite(away_sp, home_sp, away, home, pitchers, teams)

        comp_str = f"{r['composite']:+.1f}"
        band_col = GREEN if r["band"] in ("8+", "5-8") else (AMBER if r["band"] == "2-5" else DIM)

        status = ""
        if r["qualifies"]:
            status = c("  ★ QUALIFIES", GREEN, BOLD)
            qualifying.append((g, r))
        elif r["missing_sp"]:
            status = c("  ⚠ MISSING SP", AMBER)

        print(f"  {c(away, BOLD)} @ {c(home, BOLD)}")
        print(f"    Away SP: {away_sp}" +
              (f"  gap:{r['away_sp_data']['gap']:+.3f} HH%:{r['away_sp_data']['hard_hit']:.0f}" if r["away_sp_data"] else "  [not in file]"))
        print(f"    Home SP: {home_sp}" +
              (f"  gap:{r['home_sp_data']['gap']:+.3f} HH%:{r['home_sp_data']['hard_hit']:.0f}" if r["home_sp_data"] else "  [not in file]"))
        print(f"    SP:{r['sp_edge']:+.1f}  BAT:{r['bat_edge']:+.1f}  Park:{r['park_adj']:+.1f}  "
              f"→ Adj composite: {c(comp_str, band_col, BOLD)} ({r['band']})  "
              f"Model:{r['model']}  Aligned:{'✓' if r['aligned'] else '✗'}{status}")
        print()

    print(c("─" * 80, DIM))

    if qualifying:
        print(c(f"\n  ★ {len(qualifying)} QUALIFYING PLAY(S)\n", GREEN, BOLD))
        for g, r in qualifying:
            away = g["away_team"]; home = g["home_team"]
            print(c(f"  {away} @ {home}", BOLD))
            print(f"    Composite: {r['composite']:+.1f} ({r['band']})  "
                  f"Model favors: {r['model']}")
            if r["away_sp_data"]:
                d = r["away_sp_data"]
                lbl = "LUCKY→REGRESS" if d["gap"] > 0.02 else ("UNLUCKY→IMPROVE" if d["gap"] < -0.02 else "neutral")
                print(f"    Away SP {g['away_pitcher']}: gap {d['gap']:+.3f} HH%:{d['hard_hit']:.0f} K%:{d['k_pct']:.0f} → {lbl}")
            if r["home_sp_data"]:
                d = r["home_sp_data"]
                lbl = "LUCKY→REGRESS" if d["gap"] > 0.02 else ("UNLUCKY→IMPROVE" if d["gap"] < -0.02 else "neutral")
                print(f"    Home SP {g['home_pitcher']}: gap {d['gap']:+.3f} HH%:{d['hard_hit']:.0f} K%:{d['k_pct']:.0f} → {lbl}")
            if r["away_bat_data"]:
                b = r["away_bat_data"]
                print(f"    Away BAT ({away}): xwOBA {b['xwoba']} Barrel%:{b['barrel']:.1f}")
            if r["home_bat_data"]:
                b = r["home_bat_data"]
                print(f"    Home BAT ({home}): xwOBA {b['xwoba']} Barrel%:{b['barrel']:.1f}")
            print()
    else:
        print(c("  No plays qualify today. Pass.\n", DIM))

    print(c("═" * 80, BOLD))
    return qualifying


# ─────────────────────────────────────────────────────────────────────────────
# Load probable pitchers from CSV
# ─────────────────────────────────────────────────────────────────────────────

def load_games_csv(path: Path) -> list[dict]:
    if not path or not path.exists():
        return []
    games = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            games.append(row)
    return games


# ─────────────────────────────────────────────────────────────────────────────
# XLS matchup parser (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

def parse_matchup_xls(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    soup = BeautifulSoup(raw, "html.parser")
    rows = soup.select("tbody tr")
    matchups = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 14:
            continue
        try:
            def _txt(el): return el.get_text(strip=True)
            def _int(el):
                try: return int(_txt(el) or 0)
                except: return 0
            def _stat(el):
                t = _txt(el)
                if not t or t == ".": return 0.0
                try: return float(t)
                except: return 0.0

            pitcher = _txt(cells[1]); batter = _txt(cells[2])
            pa = _int(cells[4])
            ba = _stat(cells[10]); obp = _stat(cells[11])
            slg = _stat(cells[12]); ops = _stat(cells[13])

            if pitcher:
                matchups.append({
                    "pitcher": pitcher, "batter": batter,
                    "pa": pa, "ba": ba, "obp": obp, "slg": slg, "ops": ops,
                    "low_sample": pa < MIN_PA,
                })
        except Exception:
            continue
    return matchups


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MLB analysis with GitHub auto-fetch"
    )
    parser.add_argument("--no-fetch",      action="store_true",
                        help="Skip GitHub fetch, use local files only")
    parser.add_argument("--fetch-only",    action="store_true",
                        help="Fetch data and exit without analysis")
    parser.add_argument("--force-fetch",   action="store_true",
                        help="Force re-fetch even if files are fresh")
    parser.add_argument("--auto",          action="store_true",
                        help="Skip validation gate")
    parser.add_argument("--composite-only",action="store_true",
                        help="Run composite model only (no XLS needed)")
    parser.add_argument("--xls",           default=str(XLS_PATH),
                        help="Path to Sports Reference matchup XLS")
    parser.add_argument("--out-dir",       default=str(OUTPUT_DIR),
                        help="Output directory")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ── Step 1: Auto-fetch fresh data from GitHub ──────────────────────────
    sp_path = None
    if not args.no_fetch:
        if GITHUB_RAW_BASE == "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data":
            print("[AUTO-FETCH] GitHub repo not configured — set GITHUB_RAW_BASE in script.",
                  file=sys.stderr)
            print("             Run with --no-fetch to skip, or update GITHUB_RAW_BASE.",
                  file=sys.stderr)
        else:
            _, sp_path = auto_fetch(force=args.force_fetch)

    if args.fetch_only:
        print("Data fetched. Exiting (--fetch-only).")
        return

    # ── Step 2: Load models ────────────────────────────────────────────────
    print(f"[MODEL] Loading Statcast data...", file=sys.stderr)
    pitchers = load_pitcher_statcast(STATS_PATH)
    teams    = load_team_batting(STATCAST_PATH)
    print(f"  → {len(pitchers)} pitchers, {len(teams)} teams", file=sys.stderr)

    # ── Step 3: Load games ─────────────────────────────────────────────────
    games = []
    if sp_path and sp_path.exists():
        games = load_games_csv(sp_path)
        print(f"[GAMES] Loaded {len(games)} games from remote fetch", file=sys.stderr)
    else:
        # Try local cache
        cached = out / f"probable_pitchers_{datetime.now().strftime('%Y%m%d')}.csv"
        if cached.exists():
            games = load_games_csv(cached)
            print(f"[GAMES] Loaded {len(games)} games from local cache", file=sys.stderr)
        else:
            print("[GAMES] No game data found. Provide SP data or configure GitHub repo.",
                  file=sys.stderr)

    # ── Step 4: Composite model ────────────────────────────────────────────
    if games:
        qualifying = print_composite_report(games, pitchers, teams)

        # Save composite report
        today_str = datetime.now().strftime("%Y%m%d")
        report_path = out / f"composite_{today_str}.csv"
        if qualifying:
            with open(report_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["game","away_sp","home_sp","composite","band",
                                 "model","aligned","qualifies"])
                for g, r in qualifying:
                    writer.writerow([
                        f"{g['away_team']}@{g['home_team']}",
                        g.get("away_pitcher",""),
                        g.get("home_pitcher",""),
                        r["composite"], r["band"], r["model"],
                        r["aligned"], r["qualifies"]
                    ])
            print(f"  [✓] Composite report → {report_path}")

    # ── Step 5: XLS matchup data (optional) ───────────────────────────────
    if not args.composite_only:
        matchups = parse_matchup_xls(args.xls)
        if matchups:
            print(f"\n[XLS] Parsed {len(matchups)} pitcher-batter matchup rows", file=sys.stderr)
        else:
            print(f"\n[XLS] No matchup data found at {args.xls}", file=sys.stderr)


if __name__ == "__main__":
    main()
