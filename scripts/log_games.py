#!/usr/bin/env python3
"""
log_games.py
============
Logs ALL games from today's slate to data/game_log.csv —
composite scores, lines, model direction, and alignment,
regardless of whether we placed a bet.

Run AFTER the daily analysis session, once final scores are known.
Optionally pass --scores to fill in actual results.

Usage:
    python scripts/log_games.py                    # log today's composite data
    python scripts/log_games.py --date 2026-06-03  # log a specific date
    
Output: data/game_log.csv (appends; creates if missing)

Schema:
    game_date, away_team, home_team, away_sp, home_sp,
    away_gap, home_gap, sp_edge, bat_edge, bp_edge, park_adj,
    composite, band, model_dir, aligned, alignment_type, qualified,
    away_ml, home_ml, away_rl, home_rl, total,
    away_score, home_score, model_correct,
    bet_placed, bet_description, bet_result,
    notes, logged_at
"""

import csv, io, json, os, sys, urllib.request
from datetime import date, datetime
from pathlib import Path

DATA_DIR  = Path("data")
LOG_FILE  = DATA_DIR / "game_log.csv"
TIMEOUT   = 15

NORM = {'TB':'TBR','KC':'KCR','SD':'SDP','SF':'SFG','AZ':'ARI'}
PARK = {'COL':-3.0,'BOS':-1.5,'NYY':-1.5,'CHC':-1.0,'CIN':-1.0,
        'TEX':-0.5,'HOU':-0.5,'SDP':+1.5,'SFG':+1.5,'TBR':+0.5,'TOR':+0.5,'MIN':+0.5}

FIELDS = [
    'game_date','away_team','home_team','away_sp','home_sp',
    'away_gap','home_gap','sp_edge','bat_edge','bp_edge','park_adj',
    'composite','band','model_dir','aligned','alignment_type','qualified',
    'away_ml','home_ml','away_rl','home_rl','total',
    'away_score','home_score','model','lean',
    'bet_placed','bet_description','bet_result',
    'notes','logged_at',
]

GITHUB_RAW = "https://raw.githubusercontent.com/johnnydoe314/mlb-data/main/data"

def fetch(path):
    url = f"{GITHUB_RAW}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "log_games/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


# ── Pitcher projection constants ──────────────────────────────────────────────
_LEAGUE_P = {"gap": 0.000, "xwoba": 0.318, "woba": 0.318,
              "hard_hit": 37.0, "k_pct": 22.0, "bb_pct": 8.5, "whiff": 25.0}
_YR_W      = {2026: 0.50, 2025: 0.25, 2024: 0.15, 2023: 0.10}
_PA_START  = 450   # ≈ 120 IP starter threshold
_PA_RELIEF = 160   # ≈ 40 IP reliever threshold


def _project_pitcher(seasons):
    """Recency-weighted, sample-size-adjusted projection across up to 4 seasons."""
    threshold = _PA_START if any(d["pa"] >= 350 for d in seasons.values()) else _PA_RELIEF
    eff = {yr: (0.0 if yr not in seasons
                else bw * min(1.0, seasons[yr]["pa"] / threshold))
           for yr, bw in _YR_W.items()}
    total_eff = sum(eff.values())
    league_w  = max(0.0, 1.0 - total_eff)

    proj = {}
    for stat in ("gap", "xwoba", "woba", "hard_hit", "whiff"):
        val = sum(seasons[yr][stat] * eff[yr]
                  for yr in _YR_W if yr in seasons and eff[yr] > 0)
        proj[stat] = round(val + _LEAGUE_P[stat] * league_w, 4)

    for stat in ("k_pct", "bb_pct"):
        cnt = sum(seasons[yr][stat] * seasons[yr]["pa"] * _YR_W[yr]
                  for yr in _YR_W if yr in seasons and eff[yr] > 0)
        pa  = sum(seasons[yr]["pa"] * _YR_W[yr]
                  for yr in _YR_W if yr in seasons and eff[yr] > 0)
        raw = (cnt / pa) if pa > 0 else _LEAGUE_P[stat]
        proj[stat] = round(raw * (1.0 - league_w) + _LEAGUE_P[stat] * league_w, 2)

    yrs = sorted(yr for yr in _YR_W if yr in seasons)
    proj["years_used"]   = yrs
    proj["league_blend"] = round(league_w, 3)
    proj["pa"]           = seasons[max(yrs)]["pa"] if yrs else 0
    return proj


def load_pitchers(content):
    """Multi-year recency-weighted projection (2023-2026), regressed to league avg."""
    MIN_PA = 30
    all_seas = {}
    reader = csv.reader(io.StringIO(content.lstrip('\ufeff')))
    hdrs = [h.strip().strip('"') for h in next(reader)]
    for row in reader:
        d = dict(zip(hdrs, row))
        name = d.get('last_name, first_name', '').strip()
        if not name: continue
        try:
            yr = int(d.get('year', 0) or 0)
            pa = int(d.get('pa',   0) or 0)
            if yr not in _YR_W or pa < MIN_PA: continue
            entry = {
                'pa':       pa,
                'gap':      round(float(d.get('woba', 0) or 0) - float(d.get('xwoba', 0) or 0), 3),
                'woba':     float(d.get('woba',             0) or 0),
                'xwoba':    float(d.get('xwoba',            0) or 0),
                'hard_hit': float(d.get('hard_hit_percent', 0) or 0),
                'k_pct':    float(d.get('k_percent',        0) or 0),
                'bb_pct':   float(d.get('bb_percent',       0) or 0),
                'whiff':    float(d.get('whiff_percent',    0) or 0),
            }
            prev = all_seas.setdefault(name, {}).get(yr)
            if prev is None or pa > prev['pa']:
                all_seas[name][yr] = entry
        except: pass
    return {name: _project_pitcher(seas) for name, seas in all_seas.items()}


def load_teams(content):
    """Load team batting xwOBA from team_batting.csv.
    Columns: team, pa, xwoba, woba, hard_hit, barrel_pct, avg_ev
    """
    t = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('team', '').strip().upper()
        if not tm: continue
        try:
            t[tm] = {
                'xwoba':      float(row.get('xwoba', 0) or 0),
                'woba':       float(row.get('woba',  0) or 0),
                'hard_hit':   float(row.get('hard_hit', 0) or 0),
                'barrel_pct': float(row.get('barrel_pct', 0) or 0),
                'avg_ev':     float(row.get('avg_ev', 0) or 0),
                'pa':         int(float(row.get('pa', 0) or 0)),
            }
        except: pass
    return t


def load_bullpen(content):
    bp = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('team','').strip()
        fat = float(row.get('fatigue_score',1.0) or 1.0)
        gap = float(row.get('bullpen_gap',0) or 0)
        if tm:
            bp[tm] = {'gap': gap, 'fat': fat}
    return bp


def compute_composite(asn, hsn, at, ht, pitchers, teams, bullpen):
    a  = pitchers.get(asn)
    h  = pitchers.get(hsn)
    ab = teams.get(at)
    hb = teams.get(ht)
    ba = bullpen.get(at, {'gap':0,'fat':1.0})
    hb_bp = bullpen.get(ht, {'gap':0,'fat':1.0})

    # CORRECTED formula: positive gap = unlucky = helps team
    sp = 0.0
    if a: sp += (a['gap'] * 100)
    if h: sp -= (h['gap'] * 100)

    bat = ((ab['xwoba'] - hb['xwoba']) * 100) if ab and hb else 0.0

    # BP: quality term (gap × availability) + freshness term (direct fatigue edge)
    BP_FRESH_SCALE = 2.0
    quality_term   = (ba['gap']*ba['fat'] - hb_bp['gap']*hb_bp['fat']) * -50
    freshness_term = (ba['fat'] - hb_bp['fat']) * BP_FRESH_SCALE
    bp = round(quality_term + freshness_term, 2)

    park = PARK.get(ht, 0)
    raw  = round(sp + bat + bp, 2)
    adj  = round(raw + (park if raw > 0 else -park if raw < 0 else 0), 2)
    aa   = abs(adj)
    band = '8+' if aa>=8 else('5-8' if aa>=5 else('2-5' if aa>=2 else '0-2'))
    model = 'AWAY' if adj>2 else('HOME' if adj<-2 else 'NEUT')
    std  = (sp>1.5 and bat>1.5) or (sp<-1.5 and bat<-1.5)
    spd  = abs(sp)>=3.0 and abs(bat)<=1.5
    aln  = std or (spd and aa>=5)
    miss = (asn!='TBD' and not a) or (hsn!='TBD' and not h)
    qual = aa>=5 and aln and not miss

    return {
        'sp_edge': round(sp,2), 'bat_edge': round(bat,2),
        'bp_edge': bp, 'park_adj': park, 'composite': adj,
        'band': band, 'model_dir': model,
        'aligned': aln, 'alignment_type': 'BILATERAL' if std else ('SP-DOM' if spd else 'NONE'),
        'qualified': qual,
        'away_gap': a['gap'] if a else '',
        'home_gap': h['gap'] if h else '',
    }


def load_existing_log():
    if not LOG_FILE.exists():
        return set()
    with open(LOG_FILE, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return {(r['game_date'], r['away_team'], r['home_team']) for r in rows}


def append_rows(new_rows):
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        if is_new:
            writer.writeheader()
        writer.writerows(new_rows)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Log all games with composite scores')
    parser.add_argument('--date', default='', help='Date YYYY-MM-DD (overrides GAME_DATE env)')
    args = parser.parse_args()

    # Date: CLI arg → GAME_DATE env var → today
    game_date = args.date or os.environ.get('GAME_DATE', '') or date.today().isoformat()

    # Bets: read from BETS_JSON env var (set by GitHub Actions workflow_dispatch input)
    # Format: [{"game":"TOR@ATL","desc":"ATL F5 ML","result":"WIN"},...]
    bets_map = {}   # "AT@HT" -> {"desc": str, "result": str}
    try:
        bets_raw = os.environ.get('BETS_JSON', '[]')
        for b in json.loads(bets_raw):
            key = b.get('game', '').strip()
            if key:
                bets_map[key] = {
                    'desc':   b.get('desc', ''),
                    'result': b.get('result', ''),
                }
        if bets_map:
            print(f"  [✓] Bets loaded from BETS_JSON: {list(bets_map.keys())}")
    except Exception as e:
        print(f"  [~] Could not parse BETS_JSON: {e}")

    logged_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Loading data for {game_date}...")

    def fetch_optional(path, label):
        try:
            content = fetch(path)
            print(f"  [✓] {label}")
            return content
        except Exception as e:
            print(f"  [~] {label} not found — skipping ({e})")
            return None

    # Required files — exit if missing
    stats_content = fetch_optional('stats.csv', 'stats.csv')
    pp_content    = fetch_optional('probable_pitchers.csv', 'probable_pitchers.csv')
    if not stats_content or not pp_content:
        print("  [!] Critical files missing — cannot log games.")
        sys.exit(1)

    # Optional files — degrade gracefully if absent
    sc_content      = fetch_optional('team_batting.csv', 'team_batting.csv')
    odds_content    = fetch_optional('odds.csv', 'odds.csv')
    bullpen_content = fetch_optional('bullpen.csv', 'bullpen.csv')
    fatigue_content = fetch_optional('bullpen_fatigue.csv', 'bullpen_fatigue.csv')

    # Merge bullpen + fatigue (empty dicts if files missing)
    bp_raw  = {r['team']: r for r in csv.DictReader(io.StringIO(bullpen_content))} \
              if bullpen_content else {}
    fat_raw = {r['team']: r for r in csv.DictReader(io.StringIO(fatigue_content))} \
              if fatigue_content else {}
    bullpen = {}
    for tm in set(list(bp_raw.keys()) + list(fat_raw.keys())):
        gap = float(bp_raw.get(tm,{}).get('bullpen_gap',0) or 0)
        fat = float(fat_raw.get(tm,{}).get('fatigue_score',1.0) or 1.0)
        bullpen[tm] = {'gap': gap, 'fat': fat}

    pitchers = load_pitchers(stats_content)
    teams    = load_teams(sc_content) if sc_content else {}

    # Odds map (empty if odds.csv missing)
    odds_map = {}
    if odds_content:
        for r in csv.DictReader(io.StringIO(odds_content)):
            at = NORM.get(r['away_team'], r['away_team'])
            ht = NORM.get(r['home_team'], r['home_team'])
            odds_map[(at,ht)] = r
    else:
        print("  [~] No odds data — lines will be blank in log")

    # Games
    games = []
    for row in csv.DictReader(io.StringIO(pp_content)):
        at = NORM.get(row['away_team'], row['away_team'])
        ht = NORM.get(row['home_team'], row['home_team'])
        games.append({
            'at': at, 'ht': ht,
            'asp': row.get('away_pitcher','TBD'),
            'hsp': row.get('home_pitcher','TBD'),
            'game_date': row.get('game_date', game_date),   # use CSV date if available
        })

    existing = load_existing_log()
    new_rows = []

    for g in games:
        at,ht,asn,hsn = g['at'],g['ht'],g['asp'],g['hsp']
        row_date = g.get('game_date', game_date)           # each game's own date
        key = (row_date, at, ht)
        if key in existing:
            print(f"  SKIP {at}@{ht} ({row_date}) — already logged")
            continue

        c = compute_composite(asn, hsn, at, ht, pitchers, teams, bullpen)
        o = odds_map.get((at,ht), {})

        bet_key = f"{at}@{ht}"
        bet_info = bets_map.get(bet_key, {})

        row = {
            'game_date':      g.get('game_date', game_date),   # prefer CSV date over today()
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
            'aligned':        int(c['aligned']),
            'alignment_type': c['alignment_type'],
            'qualified':      int(c['qualified']),
            'away_ml':        o.get('away_ml',''),
            'home_ml':        o.get('home_ml',''),
            'away_rl':        o.get('away_rl',''),
            'home_rl':        o.get('home_rl',''),
            'total':          o.get('total',''),
            'away_score':     '',
            'home_score':     '',
            'model':  '',   # 1/0 only when qualified play result is known
            'lean':   '',   # 1/0 for all directional leans once scores are known
            'bet_placed':     1 if bet_info.get('desc') else 0,
            'bet_description': bet_info.get('desc', ''),
            'bet_result':      bet_info.get('result', ''),
            'notes':          '',
            'logged_at':      logged_at,
        }
        new_rows.append(row)
        qual_str = ' ★ QUALIFIES' if c['qualified'] else ''
        print(f"  {at}@{ht:<7} {c['composite']:+.1f} {c['band']:<5} {c['model_dir']:<5} {c['alignment_type']:<10}{qual_str}")

    if new_rows:
        append_rows(new_rows)
        print(f"\n  [✓] {LOG_FILE} — {len(new_rows)} new rows added")
    else:
        print("\n  Nothing new to log.")


if __name__ == "__main__":
    main()
