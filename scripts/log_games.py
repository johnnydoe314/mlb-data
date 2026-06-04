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
    'away_score','home_score','model_correct',
    'bet_placed','bet_description','bet_result',
    'notes','logged_at',
]

GITHUB_RAW = "https://raw.githubusercontent.com/johnnydoe314/mlb-data/main/data"

def fetch(path):
    url = f"{GITHUB_RAW}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "log_games/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


def load_pitchers(content):
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
                'pa': pa,
                'gap': round(float(d.get('woba',0) or 0) -
                             float(d.get('xwoba',0) or 0), 3),
            }
        except: pass
    return p


def load_teams(content):
    t = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('Team','').strip().upper()
        if not tm or tm == 'MLB': continue
        try:
            t[tm] = float(row.get('xwOBA',0))
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

    bat = (ab - hb) * 100 if ab and hb else 0.0

    bp = round((ba['gap']*ba['fat'] - hb_bp['gap']*hb_bp['fat']) * -50, 2) \
         if ba and hb_bp else 0.0

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
    parser.add_argument('--date', default=date.today().isoformat(), help='Date YYYY-MM-DD')
    args = parser.parse_args()

    game_date = args.date
    logged_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Loading data for {game_date}...")

    try:
        stats_content    = fetch('stats.csv')
        pp_content       = fetch('probable_pitchers.csv')
        sc_content       = fetch('statcast_hitting_2026.csv')
        odds_content     = fetch('odds.csv')
        bullpen_content  = fetch('bullpen.csv')
        fatigue_content  = fetch('bullpen_fatigue.csv')
    except Exception as e:
        print(f"  [!] Fetch error: {e}")
        sys.exit(1)

    # Merge bullpen + fatigue
    bp_raw = {r['team']: r for r in csv.DictReader(io.StringIO(bullpen_content))}
    fat_raw = {r['team']: r for r in csv.DictReader(io.StringIO(fatigue_content))}
    bullpen = {}
    for tm in set(list(bp_raw.keys()) + list(fat_raw.keys())):
        gap = float(bp_raw.get(tm,{}).get('bullpen_gap',0) or 0)
        fat = float(fat_raw.get(tm,{}).get('fatigue_score',1.0) or 1.0)
        bullpen[tm] = {'gap': gap, 'fat': fat}

    pitchers = load_pitchers(stats_content)
    teams    = load_teams(sc_content)

    # Odds map
    odds_map = {}
    for r in csv.DictReader(io.StringIO(odds_content)):
        at = NORM.get(r['away_team'], r['away_team'])
        ht = NORM.get(r['home_team'], r['home_team'])
        odds_map[(at,ht)] = r

    # Games
    games = []
    for row in csv.DictReader(io.StringIO(pp_content)):
        at = NORM.get(row['away_team'], row['away_team'])
        ht = NORM.get(row['home_team'], row['home_team'])
        games.append({
            'at': at, 'ht': ht,
            'asp': row.get('away_pitcher','TBD'),
            'hsp': row.get('home_pitcher','TBD'),
        })

    existing = load_existing_log()
    new_rows = []

    for g in games:
        at,ht,asn,hsn = g['at'],g['ht'],g['asp'],g['hsp']
        key = (game_date, at, ht)
        if key in existing:
            print(f"  SKIP {at}@{ht} — already logged")
            continue

        c = compute_composite(asn, hsn, at, ht, pitchers, teams, bullpen)
        o = odds_map.get((at,ht), {})

        row = {
            'game_date':      game_date,
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
            'away_score':     '',   # fill in after game
            'home_score':     '',   # fill in after game
            'model_correct':  '',   # fill in after game
            'bet_placed':     0,
            'bet_description':'',
            'bet_result':     '',
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
