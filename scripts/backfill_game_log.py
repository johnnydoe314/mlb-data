#!/usr/bin/env python3
"""
backfill_game_log.py
====================
Populates game_log.csv with historical games using actual starting
pitchers and final scores from the MLB Stats API.

Composite scores are approximations — they use the current stats.csv
(multi-year weighted) rather than the stats as they stood on each game
date. Gaps shift slightly over the season but are reasonably stable
for retrospective analysis.

Usage:
    python scripts/backfill_game_log.py --start 2026-05-01 --end 2026-06-03
    python scripts/backfill_game_log.py --start 2026-04-01  # from opening day

Skips dates already in game_log.csv. Safe to re-run.
"""

import csv, io, json, sys, time, urllib.request, urllib.error
from datetime import date, timedelta, datetime
from pathlib import Path

DATA_DIR = Path("data")
LOG_FILE = DATA_DIR / "game_log.csv"
TIMEOUT  = 20

NORM = {'TB':'TBR','KC':'KCR','SD':'SDP','SF':'SFG','AZ':'ARI',
        'Arizona Diamondbacks':'ARI','Atlanta Braves':'ATL',
        'Baltimore Orioles':'BAL','Boston Red Sox':'BOS',
        'Chicago Cubs':'CHC','Chicago White Sox':'CWS',
        'Cincinnati Reds':'CIN','Cleveland Guardians':'CLE',
        'Colorado Rockies':'COL','Detroit Tigers':'DET',
        'Houston Astros':'HOU','Kansas City Royals':'KCR',
        'Los Angeles Angels':'LAA','Los Angeles Dodgers':'LAD',
        'Miami Marlins':'MIA','Milwaukee Brewers':'MIL',
        'Minnesota Twins':'MIN','New York Mets':'NYM',
        'New York Yankees':'NYY','Oakland Athletics':'ATH',
        'Athletics':'ATH','Philadelphia Phillies':'PHI',
        'Pittsburgh Pirates':'PIT','San Diego Padres':'SDP',
        'Seattle Mariners':'SEA','San Francisco Giants':'SFG',
        'St. Louis Cardinals':'STL','Tampa Bay Rays':'TBR',
        'Texas Rangers':'TEX','Toronto Blue Jays':'TOR',
        'Washington Nationals':'WSH'}

PARK = {'COL':-3.0,'BOS':-1.5,'NYY':-1.5,'CHC':-1.0,'CIN':-1.0,
        'TEX':-0.5,'HOU':-0.5,'SDP':+1.5,'SFG':+1.5,'TBR':+0.5,
        'TOR':+0.5,'MIN':+0.5}

FIELDS = [
    'game_date','away_team','home_team','away_sp','home_sp',
    'away_gap','home_gap','sp_edge','bat_edge','bp_edge','park_adj',
    'composite','band','model_dir','aligned','alignment_type','qualified',
    'away_ml','home_ml','away_rl','home_rl','total',
    'away_score','home_score','model_correct',
    'bet_placed','bet_description','bet_result',
    'notes','logged_at',
]

GITHUB_RAW = "https://raw.githubusercontent.com/johnnydoe314/mlb-data/main/data"


# ── API helpers ──────────────────────────────────────────────────────────────

def api_get(url, retries=3):
    for i in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "mlb-backfill/1.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i < retries:
                time.sleep(i * 2)
            else:
                raise
        except Exception:
            if i < retries:
                time.sleep(2)
            else:
                raise


def fetch_raw(path):
    url = f"{GITHUB_RAW}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


# ── Data loaders ─────────────────────────────────────────────────────────────

def load_pitchers():
    content = fetch_raw("stats.csv")
    p = {}
    reader = csv.reader(io.StringIO(content))
    hdrs = [h.strip().strip('"') for h in next(reader)]
    for row in reader:
        d = dict(zip(hdrs, row))
        if d.get('year','').strip() != '2026': continue
        name = d.get('last_name, first_name','').strip()
        if not name: continue
        try:
            pa = int(d.get('pa',0) or 0)
            if name in p and pa <= p[name]['pa']: continue
            p[name] = {
                'pa':    pa,
                'gap':   round(float(d.get('woba',0) or 0) -
                               float(d.get('xwoba',0) or 0), 3),
                'k_pct': float(d.get('k_percent',0) or 0),
                'bb_pct':float(d.get('bb_percent',0) or 0),
            }
        except: pass
    return p


def load_teams():
    try:
        content = fetch_raw("statcast_hitting_2026.csv")
    except Exception:
        return {}
    t = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('Team','').strip().upper()
        if not tm or tm == 'MLB': continue
        try:
            t[tm] = float(row.get('xwOBA', row.get('xwoba', 0)) or 0)
        except: pass
    return t


# ── Composite ────────────────────────────────────────────────────────────────

def compute(asn, hsn, at, ht, pitchers, teams):
    a = pitchers.get(asn)
    h = pitchers.get(hsn)
    ab = teams.get(at, 0)
    hb = teams.get(ht, 0)

    sp = 0.0
    if a: sp += (a['gap'] * 100)
    if h: sp -= (h['gap'] * 100)

    bat = (ab - hb) * 100 if ab and hb else 0.0

    park = PARK.get(ht, 0)
    raw  = round(sp + bat, 2)
    adj  = round(raw + (park if raw > 0 else -park if raw < 0 else 0), 2)
    aa   = abs(adj)

    band  = '8+' if aa>=8 else('5-8' if aa>=5 else('2-5' if aa>=2 else '0-2'))
    model = 'AWAY' if adj>2 else('HOME' if adj<-2 else 'NEUT')
    std   = (sp>1.5 and bat>1.5) or (sp<-1.5 and bat<-1.5)
    spd   = abs(sp)>=3.0 and abs(bat)<=1.5
    aln   = std or (spd and aa>=5)
    miss  = (asn!='TBD' and not a) or (hsn!='TBD' and not h)
    qual  = aa>=5 and aln and not miss

    return {
        'sp_edge': round(sp,2), 'bat_edge': round(bat,2),
        'bp_edge': 0.0, 'park_adj': park, 'composite': adj,
        'band': band, 'model_dir': model, 'aligned': int(aln),
        'alignment_type': 'BILATERAL' if std else ('SP-DOM' if spd else 'NONE'),
        'qualified': int(qual),
        'away_gap': a['gap'] if a else '',
        'home_gap': h['gap'] if h else '',
    }


# ── MLB Stats API: schedule + starters ───────────────────────────────────────

def get_schedule(start: str, end: str):
    url = (f"https://statsapi.mlb.com/api/v1/schedule"
           f"?sportId=1&startDate={start}&endDate={end}"
           f"&hydrate=linescore&gameType=R")
    data = api_get(url)
    if not data:
        return []

    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            status = g.get("status", {}).get("abstractGameState", "")
            if status != "Final":
                continue
            teams  = g.get("teams", {})
            away_t = NORM.get(teams.get("away",{}).get("team",{}).get("name",""), "")
            home_t = NORM.get(teams.get("home",{}).get("team",{}).get("name",""), "")
            if not away_t or not home_t:
                continue
            a_score = teams.get("away",{}).get("score", "")
            h_score = teams.get("home",{}).get("score", "")
            games.append({
                "game_date": date_entry["date"],
                "gamePk":    g["gamePk"],
                "away":      away_t,
                "home":      home_t,
                "a_score":   a_score,
                "h_score":   h_score,
            })
    return games


def get_starters(game_pk):
    """Return (away_sp_name, home_sp_name) in 'Last, First' format."""
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    data = api_get(url)
    if not data:
        return "TBD", "TBD"

    starters = {}
    for side in ("away", "home"):
        pitchers = data.get("teams", {}).get(side, {}).get("pitchers", [])
        players  = data.get("teams", {}).get(side, {}).get("players", {})
        if pitchers:
            sp_id  = pitchers[0]
            sp_key = f"ID{sp_id}"
            info   = players.get(sp_key, {})
            person = info.get("person", {})
            fname  = person.get("firstName", "")
            lname  = person.get("lastName", "")
            starters[side] = f"{lname}, {fname}" if lname else "TBD"
        else:
            starters[side] = "TBD"

    return starters.get("away", "TBD"), starters.get("home", "TBD")


# ── Log helpers ───────────────────────────────────────────────────────────────

def load_existing():
    if not LOG_FILE.exists():
        return set()
    with open(LOG_FILE, newline='', encoding='utf-8') as f:
        return {(r['game_date'], r['away_team'], r['home_team'])
                for r in csv.DictReader(f)}


def append_rows(rows):
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        if is_new:
            w.writeheader()
        w.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-05-01",
                    help="Start date YYYY-MM-DD (default: 2026-05-01)")
    ap.add_argument("--end",   default=(date.today() - timedelta(days=1)).isoformat(),
                    help="End date YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="Seconds between boxscore API calls (default: 0.3)")
    args = ap.parse_args()

    print("=" * 62)
    print(f"  BACKFILL GAME LOG  {args.start} → {args.end}")
    print("=" * 62)

    # Load reference data once
    print("\n  Loading pitcher/team data from GitHub...", end="", flush=True)
    try:
        pitchers = load_pitchers()
        teams    = load_teams()
        print(f" {len(pitchers)} pitchers, {len(teams)} teams")
    except Exception as e:
        print(f"\n  [!] {e}")
        sys.exit(1)

    # Fetch all final games in the date range
    print(f"  Fetching schedule {args.start}–{args.end}...", end="", flush=True)
    games = get_schedule(args.start, args.end)
    print(f" {len(games)} final games")

    existing = load_existing()
    to_log   = [g for g in games
                if (g["game_date"], g["away"], g["home"]) not in existing]
    print(f"  Already logged: {len(games)-len(to_log)}  |  "
          f"To add: {len(to_log)}\n")

    if not to_log:
        print("  Nothing to add.")
        return

    DATA_DIR.mkdir(exist_ok=True)
    new_rows = []
    logged_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    for i, g in enumerate(to_log, 1):
        gd   = g["game_date"]
        at   = g["away"]
        ht   = g["home"]
        a_sc = g["a_score"]
        h_sc = g["h_score"]

        # Get actual starters from boxscore
        asn, hsn = get_starters(g["gamePk"])
        time.sleep(args.delay)

        c = compute(asn, hsn, at, ht, pitchers, teams)

        # model_correct
        try:
            a, h = int(a_sc), int(h_sc)
            if c['model_dir'] == 'AWAY':
                mc = 1 if a > h else (0 if a < h else '')
            elif c['model_dir'] == 'HOME':
                mc = 1 if h > a else (0 if h < a else '')
            else:
                mc = ''
        except (ValueError, TypeError):
            mc = ''

        qual_str = " ★" if c['qualified'] else ""
        print(f"  [{i:>3}/{len(to_log)}] {gd}  {at}@{ht:<4}"
              f"  {a_sc}-{h_sc}  comp:{c['composite']:+.1f} {c['band']:<4}"
              f"  {c['model_dir']:<5}  {'✅' if mc==1 else '❌' if mc==0 else '~'}"
              f"{qual_str}")

        new_rows.append({
            'game_date':      gd,
            'away_team':      at,
            'home_team':      ht,
            'away_sp':        asn,
            'home_sp':        hsn,
            'away_gap':       c['away_gap'],
            'home_gap':       c['home_gap'],
            'sp_edge':        c['sp_edge'],
            'bat_edge':       c['bat_edge'],
            'bp_edge':        c['bp_edge'],
            'park_adj':       c['park_adj'],
            'composite':      c['composite'],
            'band':           c['band'],
            'model_dir':      c['model_dir'],
            'aligned':        c['aligned'],
            'alignment_type': c['alignment_type'],
            'qualified':      c['qualified'],
            'away_ml': '', 'home_ml': '', 'away_rl': '', 'home_rl': '', 'total': '',
            'away_score':     a_sc,
            'home_score':     h_sc,
            'model_correct':  mc,
            'bet_placed':     0,
            'bet_description':'',
            'bet_result':     '',
            'notes':          'backfill — composite uses current stats (approx)',
            'logged_at':      logged_at,
        })

        # Write in batches of 50 to avoid losing progress
        if len(new_rows) % 50 == 0:
            append_rows(new_rows)
            new_rows = []
            print(f"  ... saved checkpoint")

    if new_rows:
        append_rows(new_rows)

    # Final summary
    all_rows = list(csv.DictReader(open(LOG_FILE, encoding='utf-8')))
    bf_rows  = [r for r in all_rows if r.get('notes','').startswith('backfill')]
    correct  = [r for r in bf_rows if r.get('model_correct') == '1']
    wrong    = [r for r in bf_rows if r.get('model_correct') == '0']
    qual     = [r for r in bf_rows if r.get('qualified') == '1']
    q_corr   = [r for r in qual   if r.get('model_correct') == '1']

    print(f"\n{'='*62}")
    print(f"  Done. {len(bf_rows)} backfilled games in {LOG_FILE}")
    if correct or wrong:
        total_pred = len(correct) + len(wrong)
        pct = len(correct)/total_pred*100 if total_pred else 0
        print(f"  All games:      {len(correct)}W {len(wrong)}L / {total_pred} "
              f"predicted  ({pct:.1f}%)")
    if qual:
        q_total = len([r for r in qual if r.get('model_correct') in ('0','1')])
        q_pct   = len(q_corr)/q_total*100 if q_total else 0
        print(f"  Qualifying (★): {len(q_corr)}W "
              f"{q_total-len(q_corr)}L / {q_total}  ({q_pct:.1f}%)")
    print(f"{'='*62}")


if __name__ == "__main__":
    main()
