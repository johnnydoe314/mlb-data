#!/usr/bin/env python3
"""
MLB Analysis Runner
====================
Default mode: fetch and display today's SP slate only.
Run analysis explicitly when ready.

Usage:
    python run_analysis.py              # SP data only (default)
    python run_analysis.py --analyze    # run composite model on loaded SPs
    python run_analysis.py --no-fetch   # use local files, no web fetch
    python run_analysis.py --auto       # skip validation gate (SP mode)
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing: pip install beautifulsoup4")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data"
)

DEFAULT_XLS      = "sportsref_download.xls"
DEFAULT_STATCAST  = "statcast_hitting_2026.csv"
DEFAULT_STATS     = "stats.csv"
OUTPUT_DIR        = Path("analysis_output")
MIN_PA            = 15
STALE_HOURS       = 12

PARK_ADJ = {
    "COL": -3.0, "BOS": -1.5, "NYY": -1.5, "CHC": -1.0, "CIN": -1.0,
    "TEX": -0.5, "HOU": -0.5, "SDP": +1.5, "SFG": +1.5, "TBR": +0.5,
    "TOR": +0.5, "MIN": +0.5,
}

ANSI = {
    "bold":  "\033[1m",  "green": "\033[92m", "yellow": "\033[93m",
    "red":   "\033[91m", "cyan":  "\033[96m",  "reset": "\033[0m",
    "dim":   "\033[2m",
}

def c(text, *codes):
    if not sys.stdout.isatty(): return text
    return "".join(ANSI.get(x, "") for x in codes) + text + ANSI["reset"]


# ─────────────────────────────────────────────────────────────────────────────
# SP DATA — fetch & display
# ─────────────────────────────────────────────────────────────────────────────

def is_stale(path: Path, hours: int = STALE_HOURS) -> bool:
    if not path.exists(): return True
    return (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)) \
           > timedelta(hours=hours)


def fetch_remote(filename: str, local_path: Path) -> bool:
    url = f"{GITHUB_RAW_BASE}/{filename}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "run_analysis/2.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            local_path.write_bytes(r.read())
        print(c(f"  [✓] {filename} → {local_path}", "green"), file=sys.stderr)
        return True
    except Exception as e:
        print(c(f"  [!] Fetch failed: {e}", "yellow"), file=sys.stderr)
        return False


def load_games(path: Path) -> list[dict]:
    if not path or not path.exists(): return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def display_sp_slate(games: list[dict], pitchers: dict) -> list[dict]:
    """
    Display today's SP slate cleanly.
    Flags SPs not in the stats file so you know what's missing.
    Returns games list for downstream use.
    """
    today = datetime.now().strftime("%A, %B %d %Y")
    print()
    print(c("═" * 68, "bold"))
    print(c(f"  MLB STARTING PITCHERS — {today}", "bold", "cyan"))
    print(c(f"  {len(games)} game(s) on the slate", "dim"))
    print(c("═" * 68, "bold"))
    print()

    for i, g in enumerate(games, 1):
        away     = g.get("away_team", "?")
        home     = g.get("home_team", "?")
        away_sp  = g.get("away_pitcher", "TBD")
        home_sp  = g.get("home_pitcher", "TBD")
        gtime    = g.get("game_time", "")

        # Stats availability
        a_data = pitchers.get(away_sp)
        h_data = pitchers.get(home_sp)

        a_info = (c(f"gap {a_data['gap']:+.3f} HH%:{a_data['hard_hit']:.0f} K%:{a_data['k_pct']:.0f}", "green")
                  if a_data else c("not in file", "yellow"))
        h_info = (c(f"gap {h_data['gap']:+.3f} HH%:{h_data['hard_hit']:.0f} K%:{h_data['k_pct']:.0f}", "green")
                  if h_data else c("not in file", "yellow"))

        flags = []
        if away_sp == "TBD": flags.append(c("AWAY TBD", "red"))
        if home_sp == "TBD": flags.append(c("HOME TBD", "red"))
        if not a_data and away_sp != "TBD": flags.append(c(f"{away_sp} missing stats", "yellow"))
        if not h_data and home_sp != "TBD": flags.append(c(f"{home_sp} missing stats", "yellow"))

        print(c(f"  [{i:>2}]", "dim") +
              f"  {c(away,'bold')} @ {c(home,'bold')}" +
              (f"  {c(gtime,'dim')}" if gtime else ""))
        print(f"       Away: {c(away_sp,'cyan')}  {a_info}")
        print(f"       Home: {c(home_sp,'cyan')}  {h_info}")
        if flags:
            print(f"       " + "  ".join(flags))
        print()

    print(c("─" * 68, "dim"))
    missing = sum(1 for g in games
                  if not pitchers.get(g.get("away_pitcher","")) or
                     not pitchers.get(g.get("home_pitcher","")))
    if missing:
        print(c(f"  ⚠  {missing} game(s) have SPs not in stats file.", "yellow"))
        print(c("     Update stats.csv or note before running analysis.", "dim"))
    else:
        print(c("  ✓  All SPs found in stats file.", "green"))
    print()
    print(c("  Ready. Run with --analyze to compute composites.", "dim"))
    print(c("═" * 68, "bold"))
    print()
    return games


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE MODEL
# ─────────────────────────────────────────────────────────────────────────────

def load_pitcher_statcast(path: Path) -> dict:
    pitchers = {}
    if not path.exists(): return pitchers
    with open(path, "r", encoding="utf-8-sig") as f:
        content = f.read()
    reader = csv.reader(io.StringIO(content))
    headers = [h.strip().strip('"') for h in next(reader)]
    for row in reader:
        d = dict(zip(headers, row))
        if d.get("year") != "2026": continue
        name = d.get("last_name, first_name", "").strip()
        if not name: continue
        try:
            pa = int(d.get("pa", 0) or 0)
            if name in pitchers and pa <= pitchers[name]["pa"]: continue
            pitchers[name] = {
                "pa":       pa,
                "woba":     float(d.get("woba", 0)),
                "xwoba":    float(d.get("xwoba", 0)),
                "gap":      round(float(d.get("woba",0)) - float(d.get("xwoba",0)), 3),
                "hard_hit": float(d.get("hard_hit_percent", 0)),
                "whiff":    float(d.get("whiff_percent", 0)),
                "k_pct":    float(d.get("k_percent", 0)),
            }
        except: continue
    return pitchers


def load_team_batting(path: Path) -> dict:
    teams = {}
    if not path.exists(): return teams
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            team = row.get("Team", "").strip().upper()
            if not team or team == "MLB": continue
            pa = int(row.get("PA", 0) or 0)
            if team in teams and pa <= teams[team]["pa"]: continue
            try:
                teams[team] = {
                    "pa":        pa,
                    "xwoba":     float(row.get("xwOBA", 0)),
                    "hard_hit":  float(row.get("Hard Hit%", 0)),
                    "barrel":    float(row.get("Barrel%", 0)),
                    "exit_velo": float(row.get("Exit Velocity", 0)),
                }
            except: continue
    return teams


def load_bullpen(path: Path) -> dict:
    """Load team bullpen xwOBA gap data."""
    bp = {}
    if not path.exists(): return bp
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            t = row.get("team", "").strip()
            if not t: continue
            try:
                bp[t] = {
                    "gap":   float(row.get("bullpen_gap", 0) or 0),
                    "k_pct": float(row.get("bullpen_k_pct", 0) or 0),
                    "rps":   int(row.get("pitchers_counted", 0) or 0),
                }
            except: continue
    return bp


def load_fatigue(path: Path) -> dict:
    """Load bullpen fatigue scores (from fetch_bullpen_usage.py)."""
    fat = {}
    if not path.exists(): return fat
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            t = row.get("team", "").strip()
            if not t: continue
            try:
                fat[t] = {
                    "score":  float(row.get("fatigue_score", 1.0) or 1.0),
                    "tired":  int(row.get("arms_tired", 0) or 0),
                    "hi_lev": int(row.get("high_lev_available", 1) or 1),
                }
            except: continue
    return fat


def compute(asn, hsn, at, ht, pitchers, teams, bullpen=None, fatigue=None):
    a  = pitchers.get(asn)
    h  = pitchers.get(hsn)
    ab = teams.get(at)
    hb = teams.get(ht)
    ba   = (bullpen or {}).get(at)
    hb_bp = (bullpen or {}).get(ht)
    fa   = (fatigue or {}).get(at, {"score": 1.0, "tired": 0, "hi_lev": 1})
    fh   = (fatigue or {}).get(ht, {"score": 1.0, "tired": 0, "hi_lev": 1})

    sp = 0.0
    # gap = wOBA_allowed - xwOBA_allowed
    # Positive gap → pitcher UNLUCKY (worse results than contact) → will IMPROVE → helps their team
    # Negative gap → pitcher LUCKY  (better results than contact) → will REGRESS → hurts their team
    # Away SP: positive gap improves AWAY → sp goes up; negative gap hurts AWAY → sp goes down
    # Home SP: positive gap improves HOME → sp goes down; negative gap hurts HOME → sp goes up
    if a: sp += (a["gap"] * 100)   # was: * -100 (sign was inverted — fixed)
    if h: sp -= (h["gap"] * 100)   # was: * -100 (sign was inverted — fixed)
    bat = (ab["xwoba"] - hb["xwoba"]) * 100 if ab and hb else 0.0

    # Bullpen edge with fatigue adjustment
    # Each team's gap discounted by their fatigue score (fresh=1.0, depleted<0.70)
    bp = 0.0
    if ba and hb_bp:
        bp = round((ba["gap"] * fa["score"] - hb_bp["gap"] * fh["score"]) * -50, 2)
    elif ba:
        bp = round(ba["gap"] * fa["score"] * -50, 2)
    elif hb_bp:
        bp = round(hb_bp["gap"] * fh["score"] * 50, 2)

    raw = round(sp + bat + bp, 2)
    park = PARK_ADJ.get(ht, 0)
    adj  = round(raw + (park if raw > 0 else -park if raw < 0 else 0), 2)
    aa   = abs(adj)
    band = "8+" if aa>=8 else ("5-8" if aa>=5 else ("2-5" if aa>=2 else "0-2"))
    model = "AWAY" if adj>2 else ("HOME" if adj<-2 else "NEUT")
    std_bil  = (sp > 1.5 and bat > 1.5) or (sp < -1.5 and bat < -1.5)
    sp_dom   = abs(sp) >= 3.0 and abs(bat) <= 1.5
    aligned  = std_bil or (sp_dom and aa >= 5)
    missing  = (asn and not a) or (hsn and not h)
    qualifies = aa >= 5 and aligned and not missing

    # Fatigue flags
    fat_flags = []
    if fa["score"] < 0.50: fat_flags.append(f"🔴{at}_BP_DEPLETED({fa['tired']}tired)")
    elif fa["score"] < 0.70: fat_flags.append(f"🟡{at}_BP_TIRED({fa['tired']}tired)")
    if fh["score"] < 0.50: fat_flags.append(f"🔴{ht}_BP_DEPLETED({fh['tired']}tired)")
    elif fh["score"] < 0.70: fat_flags.append(f"🟡{ht}_BP_TIRED({fh['tired']}tired)")

    return dict(sp_edge=sp, bat_edge=bat, bp_edge=bp, park=park, raw=raw,
                adj=adj, abs=aa, band=band, model=model,
                aligned=aligned, missing=missing, qualifies=qualifies,
                sp_dominant=(sp_dom and not std_bil),
                away_sp=a, home_sp=h, away_bat=ab, home_bat=hb,
                away_bp=ba, home_bp=hb_bp,
                away_fatigue=fa, home_fatigue=fh,
                fat_flags=fat_flags)


def run_composite_analysis(games: list[dict], pitchers: dict, teams: dict,
                            bullpen: dict = None, fatigue: dict = None):
    print()
    print(c("═" * 68, "bold"))
    print(c("  COMPOSITE MODEL — FULL ANALYSIS", "bold", "cyan"))
    print(c("═" * 68, "bold"))
    print()
    print(f"  {'Game':<13} {'SP':>6} {'BAT':>6} {'BP':>5} {'PRK':>5} {'ADJ':>7} "
          f"{'Band':<5} {'Aln':>4} {'Model'}")
    print(c("  " + "─" * 68, "dim"))

    qualifying = []

    for g in games:
        away   = g.get("away_team", "?")
        home   = g.get("home_team", "?")
        asn    = g.get("away_pitcher", "TBD")
        hsn    = g.get("home_pitcher", "TBD")
        r      = compute(asn, hsn, away, home, pitchers, teams, bullpen, fatigue)

        adj_col = ("green" if r["band"] == "8+" else
                   "yellow" if r["band"] == "5-8" else "dim")
        aln_str = c("✓","green") if r["aligned"] else c("✗","dim")
        flag    = c("  ★","green","bold") if r["qualifies"] else \
                  (c("  ⚑","yellow") if (r["sp_dominant"] and not r["missing"]) else "")

        adj_str = c(f"{r['adj']:+.1f}", adj_col)
        bp_str = f"{r.get('bp_edge',0.0):>+5.2f}"
        fat_str = " " + " ".join(r.get("fat_flags",[])) if r.get("fat_flags") else ""
        print(f"  {away}@{home:<9} "
              f"{r['sp_edge']:>+6.1f} {r['bat_edge']:>+6.1f} "
              f"{bp_str} {r['park']:>+5.1f} {adj_str:>7} "
              f"{r['band']:<5} {aln_str:>4}  {r['model']}{flag}{fat_str}")

        if r["qualifies"]:
            qualifying.append((g, r))

    print()
    if qualifying:
        print(c("═" * 68, "bold"))
        print(c(f"  ★ {len(qualifying)} QUALIFYING PLAY(S)", "green", "bold"))
        print(c("═" * 68, "bold"))
        for g, r in qualifying:
            away = g["away_team"]; home = g["home_team"]
            asn  = g["away_pitcher"]; hsn = g["home_pitcher"]
            band_col = "green" if r["band"] == "8+" else "yellow"
            suffix = " (SP-dominant)" if r["sp_dominant"] else ""
            band_str = c(r["band"], band_col, "bold")
            adj_display = c("{:+.1f}".format(r["adj"]), "bold")
            model_str = c(r["model"], "cyan")
            matchup = "  {} @ {}".format(away, home)
            print()
            print("{} ->  {} {}  Model: {}{}".format(
                c(matchup,"bold"), band_str, adj_display, model_str, suffix))
            for nm, spd, role in [(asn, r["away_sp"], "Away"), (hsn, r["home_sp"], "Home")]:
                if spd:
                    d = (c("UNLUCKY→IMPROVE","green") if spd["gap"]>0.02 else
                         c("LUCKY→REGRESS","red") if spd["gap"]<-0.02 else "neutral")
                    print(f"    {role} SP {nm}: "
                          f"gap {spd['gap']:+.3f}  HH%:{spd['hard_hit']:.0f}  "
                          f"K%:{spd['k_pct']:.0f}  →  {d}")
                else:
                    print(f"    {role} SP {nm}: {c('not in file','yellow')}")
            for tm, bat, role in [(away, r["away_bat"],"Away"), (home, r["home_bat"],"Home")]:
                if bat:
                    print(f"    {role} BAT ({tm}): xwOBA {bat['xwoba']}  "
                          f"Barrel%:{bat['barrel']:.1f}  EV:{bat['exit_velo']:.1f}")
    else:
        print(c("  No plays qualify today. Pass on all games.", "dim"))
    print()
    print(c("═" * 68, "bold"))
    return qualifying


# ─────────────────────────────────────────────────────────────────────────────
# XLS MATCHUP PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_matchup_xls(path: str) -> list[dict]:
    if not os.path.exists(path): return []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    soup = BeautifulSoup(raw, "html.parser")
    matchups = []
    for row in soup.select("tbody tr"):
        cells = row.find_all(["td","th"])
        if len(cells) < 14: continue
        try:
            def _t(el): return el.get_text(strip=True)
            def _i(el):
                try: return int(_t(el) or 0)
                except: return 0
            def _s(el):
                t = _t(el)
                if not t or t==".": return 0.0
                try: return float(t)
                except: return 0.0
            pitcher = _t(cells[1])
            if pitcher:
                matchups.append({
                    "pitcher": pitcher, "batter": _t(cells[2]),
                    "pa": _i(cells[4]),
                    "ba": _s(cells[10]), "obp": _s(cells[11]),
                    "slg": _s(cells[12]), "ops": _s(cells[13]),
                    "low_sample": _i(cells[4]) < MIN_PA,
                })
        except: continue
    return matchups


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="MLB analysis — SP display by default, --analyze to run model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run_analysis.py              # show today's SPs\n"
            "  python run_analysis.py --analyze    # run composite model\n"
            "  python run_analysis.py --no-fetch   # local files only\n"
        )
    )
    parser.add_argument("--analyze",      action="store_true",
                        help="Run composite model (requires --analyze flag)")
    parser.add_argument("--no-fetch",     action="store_true",
                        help="Skip GitHub fetch, use local files only")
    parser.add_argument("--force-fetch",  action="store_true",
                        help="Force re-fetch even if files are fresh")
    parser.add_argument("--auto",         action="store_true",
                        help="Skip validation gate")
    parser.add_argument("--xls",          default=DEFAULT_XLS)
    parser.add_argument("--statcast",     default=DEFAULT_STATCAST)
    parser.add_argument("--stats",        default=DEFAULT_STATS)
    parser.add_argument("--out-dir",      default=str(OUTPUT_DIR))
    parser.add_argument("--sp-csv",       default=None,
                        help="Explicit SP CSV path (skips fetch)")
    args = parser.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")

    # ── Load Statcast data (always, lightweight) ────────────────────────────
    pitchers = load_pitcher_statcast(Path(args.stats))
    teams    = load_team_batting(Path(args.statcast))
    # Try local path first, then data/ subdirectory (GitHub Actions layout)
    from pathlib import Path as _P
    def _find(name):
        for p in [name, f"data/{name}"]:
            if _P(p).exists(): return _P(p)
        return _P(f"data/{name}")
    bullpen  = load_bullpen(_find("bullpen.csv"))
    fatigue  = load_fatigue(_find("bullpen_fatigue.csv"))
    print(c(f"[DATA] {len(pitchers)} pitchers · {len(teams)} teams · "
            f"{len(bullpen)} bullpen · {len(fatigue)} fatigue",
            "dim"), file=sys.stderr)

    # ── Get SP data ─────────────────────────────────────────────────────────
    sp_path = None

    if args.sp_csv and Path(args.sp_csv).exists():
        sp_path = Path(args.sp_csv)
        print(c(f"[SP] Using {sp_path}", "dim"), file=sys.stderr)

    elif not args.no_fetch and GITHUB_RAW_BASE != \
            "https://raw.githubusercontent.com/YOUR_USERNAME/YOUR_REPO/main/data":
        remote_path = out / "probable_pitchers_remote.csv"
        if args.force_fetch or is_stale(remote_path, hours=2):
            print(c("[SP] Fetching from GitHub...", "dim"), file=sys.stderr)
            if fetch_remote("probable_pitchers.csv", remote_path):
                sp_path = remote_path
        else:
            sp_path = remote_path
            print(c(f"[SP] Using cached remote file ({sp_path})", "dim"),
                  file=sys.stderr)

    else:
        # Try local today's cache
        cached = out / f"probable_pitchers_{today_str}.csv"
        if cached.exists():
            sp_path = cached
            print(c(f"[SP] Using local cache ({sp_path})", "dim"), file=sys.stderr)
        else:
            print(c("[SP] No SP file found. Provide --sp-csv or configure GitHub repo.",
                    "yellow"), file=sys.stderr)

    games = load_games(sp_path) if sp_path else []

    # ─── DEFAULT MODE: SP slate display only ────────────────────────────────
    if not args.analyze:
        if games:
            display_sp_slate(games, pitchers)
            # Save for later use by --analyze
            cache_path = out / f"probable_pitchers_{today_str}.csv"
            if sp_path and sp_path != cache_path:
                import shutil
                shutil.copy(sp_path, cache_path)
        else:
            print(c("\n  No SP data available. Check your data source.\n", "yellow"))
        return

    # ─── ANALYSIS MODE: composite model ─────────────────────────────────────
    if not games:
        print(c("[ANALYZE] No game data. Run without --analyze first.", "yellow"))
        return

    qualifying = run_composite_analysis(games, pitchers, teams, bullpen, fatigue)

    # Save qualifying plays
    if qualifying:
        report_path = out / f"composite_{today_str}.csv"
        with open(report_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["game","away_sp","home_sp","composite","band",
                             "model","aligned","sp_dominant"])
            for g, r in qualifying:
                writer.writerow([
                    f"{g['away_team']}@{g['home_team']}",
                    g.get("away_pitcher",""), g.get("home_pitcher",""),
                    r["adj"], r["band"], r["model"],
                    r["aligned"], r["sp_dominant"]
                ])
        print(c(f"  [✓] Qualifying plays → {report_path}", "dim"))

    # Optional: XLS matchup data
    matchups = parse_matchup_xls(args.xls)
    if matchups:
        print(c(f"\n  [XLS] {len(matchups)} pitcher-batter matchup rows available",
                "dim"))


if __name__ == "__main__":
    main()
