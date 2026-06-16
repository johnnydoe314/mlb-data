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
    away_fa_score, home_fa_score, away_bp_tired, home_bp_tired,
    composite, band, model_dir, aligned, alignment_type, qualified,
    sp_cat, bat_cat, bp_cat, f5_rec, full_rec, run_line_flag,
    away_score, home_score,
    away_f5, home_f5, f5_total, f5_result, f5_lean, f5_correct,
    model, lean,
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

# ── Play recommendation system ────────────────────────────────────────────────
SP_GREAT, SP_GOOD, SP_BAD, SP_VBAD     =  3.0,  1.5, -1.5, -3.0
BAT_GREAT, BAT_GOOD, BAT_BAD, BAT_VBAD =  2.5,  1.5, -1.5, -2.5
BP_GREAT, BP_GOOD, BP_BAD, BP_VBAD     =  0.8,  0.3, -0.3, -0.8

def _cat5(v, great, good, bad, vbad):
    if v >= great: return 'GREAT'
    if v >= good:  return 'GOOD'
    if v >= bad:   return 'NEUTRAL'
    if v >= vbad:  return 'BAD'
    return 'VERY_BAD'

def _n3(c): return 'GOOD' if c in ('GREAT','GOOD') else ('NEUTRAL' if c=='NEUTRAL' else 'BAD')

_PLAY_LOOKUP = {
    ('GOOD','GOOD','GOOD'):(True,True), ('GOOD','GOOD','NEUTRAL'):(True,True),
    ('GOOD','GOOD','BAD'):(True,False), ('GOOD','NEUTRAL','GOOD'):(True,True),
    ('GOOD','NEUTRAL','NEUTRAL'):(True,False), ('GOOD','NEUTRAL','BAD'):(True,False),
    ('GOOD','BAD','GOOD'):(False,True), ('GOOD','BAD','NEUTRAL'):(False,False),
    ('GOOD','BAD','BAD'):(False,False), ('NEUTRAL','GOOD','GOOD'):(False,True),
    ('NEUTRAL','GOOD','NEUTRAL'):(False,True), ('NEUTRAL','GOOD','BAD'):(False,False),
    ('NEUTRAL','NEUTRAL','GOOD'):(False,False), ('NEUTRAL','NEUTRAL','NEUTRAL'):(False,False),
    ('NEUTRAL','NEUTRAL','BAD'):(False,False), ('NEUTRAL','BAD','GOOD'):(False,False),
    ('NEUTRAL','BAD','NEUTRAL'):(False,False), ('NEUTRAL','BAD','BAD'):(False,False),
    ('BAD','GOOD','GOOD'):(False,False), ('BAD','GOOD','NEUTRAL'):(False,False),
    ('BAD','GOOD','BAD'):(False,False), ('BAD','NEUTRAL','GOOD'):(False,False),
    ('BAD','NEUTRAL','NEUTRAL'):(False,False), ('BAD','NEUTRAL','BAD'):(False,False),
    ('BAD','BAD','GOOD'):(False,False), ('BAD','BAD','NEUTRAL'):(False,False),
    ('BAD','BAD','BAD'):(False,False),
}
_HOME_ONLY = {('GOOD','NEUTRAL','BAD'), ('NEUTRAL','GOOD','GOOD'), ('NEUTRAL','GOOD','NEUTRAL')}

def recommend_play(sp, bat, bp, model_dir):
    if model_dir == 'NEUT':
        return dict(sp_cat='NEUTRAL',bat_cat='NEUTRAL',bp_cat='NEUTRAL',
                    f5=False,full=False,run_line=False)
    sign = -1 if model_dir == 'HOME' else 1
    adj_sp, adj_bat, adj_bp = sp*sign, bat*sign, bp*sign
    sp_cat  = _cat5(adj_sp,  SP_GREAT,  SP_GOOD,  SP_BAD,  SP_VBAD)
    bat_cat = _cat5(adj_bat, BAT_GREAT, BAT_GOOD, BAT_BAD, BAT_VBAD)
    bp_cat  = _cat5(adj_bp,  BP_GREAT,  BP_GOOD,  BP_BAD,  BP_VBAD)
    key = (_n3(sp_cat), _n3(bat_cat), _n3(bp_cat))
    f5, full = _PLAY_LOOKUP.get(key, (False, False))
    if key in _HOME_ONLY and model_dir != 'HOME':
        if key == ('GOOD','NEUTRAL','BAD'): f5 = False
        else: full = False
    # Great SP bonus — only when opposing offense isn't dominant
    if sp_cat == 'GREAT' and bat_cat not in ('BAD','VERY_BAD'):
        f5 = True
        if (bat_cat in ('GREAT','GOOD') or bp_cat in ('GREAT','GOOD')) \
                and bp_cat not in ('BAD','VERY_BAD'):
            full = True
    if bat_cat == 'GREAT' and sp_cat in ('GREAT','GOOD') and not full:
        if bp_cat not in ('BAD','VERY_BAD'): full = True
    if sp_cat == 'VERY_BAD': f5 = full = False
    if bp_cat == 'VERY_BAD' and full: full = False
    run_line = (f5 or full) and (sp_cat == 'GREAT' or bat_cat == 'GREAT')
    return dict(sp_cat=sp_cat, bat_cat=bat_cat, bp_cat=bp_cat,
                f5=f5, full=full, run_line=run_line)

FIELDS = [
    'game_date','away_team','home_team','away_sp','home_sp',
    'away_gap','home_gap','sp_edge','bat_edge','bp_edge','park_adj',
    'away_fa_score','home_fa_score','away_bp_tired','home_bp_tired',
    'composite','band','model_dir','aligned','alignment_type','qualified',
    'sp_cat','bat_cat','bp_cat','f5_rec','full_rec','run_line_flag',
    'away_score','home_score',
    'away_f5','home_f5','f5_total','f5_result','f5_lean','f5_correct',
    'model','lean',
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
_YR_W     = {2026: 0.50, 2025: 0.25, 2024: 0.15, 2023: 0.10}
_PA_START = 450   # ≈ 120 IP starter threshold
_PA_RELIEF = 160  # ≈ 40 IP reliever threshold
_MIN_DATA_W = 0.10  # minimum normalised weight — below this, treat as insufficient


def _project_pitcher(seasons):
    """Recency-weighted projection using only real pitcher data — no league average blend.

    Weights are normalised to 1.0 so the projection reflects the pitcher's actual
    data rather than a diluted mix with league average. Returns None when the
    pitcher has too little data (normalised weight < _MIN_DATA_W).
    """
    threshold = _PA_START if any(d["pa"] >= 350 for d in seasons.values()) else _PA_RELIEF

    # Raw recency-weighted sample-size adjustment
    raw_eff = {
        yr: bw * min(1.0, seasons[yr]["pa"] / threshold)
        if yr in seasons else 0.0
        for yr, bw in _YR_W.items()
    }
    total_w = sum(raw_eff.values())

    if total_w < _MIN_DATA_W:
        return None   # insufficient data → caller sees this as MISS

    # Normalise so all weight comes from real data
    eff = {yr: w / total_w for yr, w in raw_eff.items()}

    proj = {}
    for stat in ("gap", "xwoba", "woba", "hard_hit", "whiff"):
        proj[stat] = round(
            sum(seasons[yr][stat] * eff[yr]
                for yr in _YR_W if yr in seasons and raw_eff[yr] > 0),
            4
        )
    for stat in ("k_pct", "bb_pct"):
        cnt = sum(seasons[yr][stat] * seasons[yr]["pa"] * eff[yr]
                  for yr in _YR_W if yr in seasons and raw_eff[yr] > 0)
        pa  = sum(seasons[yr]["pa"]             * eff[yr]
                  for yr in _YR_W if yr in seasons and raw_eff[yr] > 0)
        proj[stat] = round(cnt / pa, 2) if pa > 0 else 0.0

    yrs = sorted(yr for yr in _YR_W if yr in seasons)
    proj["years_used"] = yrs
    proj["data_weight"] = round(total_w, 3)  # coverage: 1.0 = full-season data
    proj["pa"]          = seasons[max(yrs)]["pa"] if yrs else 0
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
        fat   = float(row.get('fatigue_score',1.0) or 1.0)
        tired = int(float(row.get('arms_tired',0) or 0))
        gap   = float(row.get('bullpen_gap',0) or 0)
        if tm:
            bp[tm] = {'gap': gap, 'fat': fat, 'tired': tired}
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

    rec = recommend_play(round(sp,2), round(bat,2), bp, model)

    # If either starter is missing from the file, suppress all play recommendations.
    # A composite built on only one pitcher's data is unreliable.
    if miss:
        rec['f5'] = rec['full'] = rec['run_line'] = False

    return {
        'sp_edge': round(sp,2), 'bat_edge': round(bat,2),
        'bp_edge': bp, 'park_adj': park, 'composite': adj,
        'band': band, 'model_dir': model,
        'aligned': aln, 'alignment_type': 'BILATERAL' if std else ('SP-DOM' if spd else 'NONE'),
        'qualified': qual,
        'away_gap': a['gap'] if a else '',
        'home_gap': h['gap'] if h else '',
        'away_fa_score': round(ba['fat'], 3),
        'home_fa_score': round(hb_bp['fat'], 3),
        'away_bp_tired': ba.get('tired', 0),
        'home_bp_tired': hb_bp.get('tired', 0),
        'sp_cat': rec['sp_cat'], 'bat_cat': rec['bat_cat'], 'bp_cat': rec['bp_cat'],
        'f5_rec': int(rec['f5']), 'full_rec': int(rec['full']),
        'run_line_flag': int(rec['run_line']),
    }


def load_existing_log():
    """Return dict keyed by (game_date, away_team, home_team) → row dict.
    Preserves bet/score data for merge when re-running the same day."""
    if not LOG_FILE.exists():
        return {}
    with open(LOG_FILE, newline='', encoding='utf-8') as f:
        return {(r['game_date'], r['away_team'], r['home_team']): r
                for r in csv.DictReader(f)}


# Fields we always preserve from an existing row (never overwrite with blanks)
_PRESERVE_FIELDS = {
    'away_score','home_score','away_f5','home_f5','f5_total',
    'f5_result','f5_lean','f5_correct','model','lean',
    'bet_placed','bet_description','bet_result','notes',
}


def write_log(all_rows):
    """Write the complete game log, always overwriting — never appending."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_rows)


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

    existing = load_existing_log()   # dict: key → existing row
    updated_rows = []                # will hold ALL rows for final write

    # Carry forward every existing row that isn't being refreshed today
    today_date = None
    for g in games:
        if g.get('game_date'):
            today_date = g['game_date']
            break

    for key, row in existing.items():
        if row.get('game_date') != today_date:
            updated_rows.append(row)

    for g in games:
        at,ht,asn,hsn = g['at'],g['ht'],g['asp'],g['hsp']
        row_date = g.get('game_date', game_date)
        key = (row_date, at, ht)

        c = compute_composite(asn, hsn, at, ht, pitchers, teams, bullpen)

        bet_key = f"{at}@{ht}"
        bet_info = bets_map.get(bet_key, {})

        # Start with fresh composite values
        row = {
            'game_date':      row_date,
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
            'away_fa_score':  c['away_fa_score'],
            'home_fa_score':  c['home_fa_score'],
            'away_bp_tired':  c['away_bp_tired'],
            'home_bp_tired':  c['home_bp_tired'],
            'composite':      c['composite'],
            'band':           c['band'],
            'model_dir':      c['model_dir'],
            'aligned':        int(c['aligned']),
            'alignment_type': c['alignment_type'],
            'qualified':      int(c['qualified']),
            'sp_cat':         c['sp_cat'],
            'bat_cat':        c['bat_cat'],
            'bp_cat':         c['bp_cat'],
            'f5_rec':         c['f5_rec'],
            'full_rec':       c['full_rec'],
            'run_line_flag':  c['run_line_flag'],
            'away_score':     '',
            'home_score':     '',
            'away_f5':        '',
            'home_f5':        '',
            'f5_total':       '',
            'f5_result':      '',
            'f5_lean':        '',
            'f5_correct':     '',
            'model':          '',
            'lean':           '',
            'bet_placed':     1 if bet_info.get('desc') else 0,
            'bet_description': bet_info.get('desc', ''),
            'bet_result':      bet_info.get('result', ''),
            'notes':          '',
            'logged_at':      logged_at,
        }

        # Merge: preserve scores/bets/notes from any previously logged row
        if key in existing:
            old = existing[key]
            for f in _PRESERVE_FIELDS:
                if old.get(f, ''):          # only keep if previously non-empty
                    row[f] = old[f]
            action = 'REFRESH'
        else:
            action = 'NEW'

        updated_rows.append(row)
        qual_str = ' ★ QUALIFIES' if c['qualified'] else ''
        print(f"  [{action}] {at}@{ht:<7} {c['composite']:+.1f} {c['band']:<5} {c['model_dir']:<5} {c['alignment_type']:<10}{qual_str}")

    write_log(updated_rows)
    print(f"\n  [✓] {LOG_FILE} — {len(updated_rows)} total rows ({sum(1 for g in games)} today's games)")


if __name__ == "__main__":
    main()
