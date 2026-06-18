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
    away_off_score, home_off_score, away_wrc_plus, home_wrc_plus,
    away_sp_hand, home_sp_hand,
    away_off_score_matchup, home_off_score_matchup,
    away_off_score_matchup_f5, home_off_score_matchup_f5,
    away_def_score, home_def_score,
    away_def_score_f5, home_def_score_f5,
    away_sp_k_pct, home_sp_k_pct,
    away_sp_bb_pct, home_sp_bb_pct,
    away_sp_hard_hit, home_sp_hard_hit,
    away_sp_barrel, home_sp_barrel,
    away_sp_kbb, home_sp_kbb,
    away_team_k_pct, home_team_k_pct,
    away_team_barrel, home_team_barrel,
    k_pct_matchup_away, k_pct_matchup_home,
    hh_matchup_away, hh_matchup_home,
    composite, band, model_dir, aligned, alignment_type, qualified,
    sp_cat, bat_cat, bp_cat, f5_rec, full_rec, run_line_flag,
    away_score, home_score,
    away_f5, home_f5, f5_total, f5_result, f5_lean, f5_correct,
    away_innings, home_innings,
    away_sp_exit_inn, away_sp_exit_score, home_sp_exit_inn, home_sp_exit_score,
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
    'away_off_score','home_off_score','away_wrc_plus','home_wrc_plus',
    'away_sp_hand','home_sp_hand',
    'away_off_score_matchup','home_off_score_matchup',
    'away_off_score_matchup_f5','home_off_score_matchup_f5',
    'away_def_score','home_def_score',
    'away_def_score_f5','home_def_score_f5',
    'away_sp_k_pct','home_sp_k_pct',
    'away_sp_bb_pct','home_sp_bb_pct',
    'away_sp_hard_hit','home_sp_hard_hit',
    'away_sp_barrel','home_sp_barrel',
    'away_sp_kbb','home_sp_kbb',
    'away_team_k_pct','home_team_k_pct',
    'away_team_barrel','home_team_barrel',
    'k_pct_matchup_away','k_pct_matchup_home',
    'hh_matchup_away','hh_matchup_home',
    'composite','band','model_dir','aligned','alignment_type','qualified',
    'sp_cat','bat_cat','bp_cat','f5_rec','full_rec','run_line_flag',
    'away_score','home_score',
    'away_f5','home_f5','f5_total','f5_result','f5_lean','f5_correct',
    'away_innings','home_innings',
    'away_sp_exit_inn','away_sp_exit_score',
    'home_sp_exit_inn','home_sp_exit_score',
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
    """Load team batting from team_batting.csv.
    Recomputes off_score from raw components (xwOBA + BB% + HH%) each time,
    ensuring the additive formula is always applied correctly regardless of
    what value is stored in the CSV.
    """
    LG_XWOBA = 0.318; LG_BB = 8.5; LG_HH = 37.0
    WOBA_SCALE = 1.24; LG_R_PA = 0.119
    PARK_FACTOR = {
        "COL":1.10,"BOS":1.05,"NYY":1.05,"CHC":1.03,"CIN":1.03,
        "TEX":1.01,"HOU":1.01,"SDP":0.97,"SFG":0.97,"TBR":0.99,
        "TOR":1.01,"MIN":1.01,
    }

    def _off(xwoba, bb_pct, hard_hit_pct):
        """Additive off_score: league-avg fallback when components are missing."""
        bb = bb_pct       if bb_pct       > 0 else LG_BB
        hh = hard_hit_pct if hard_hit_pct > 0 else LG_HH
        return round(xwoba + (bb - LG_BB) * 0.006 * 0.30
                           + (hh - LG_HH) * 0.003 * 0.20, 4)

    def _wrc(woba, team):
        pf   = PARK_FACTOR.get(team, 1.00)
        rate = (woba - LG_XWOBA) / WOBA_SCALE + LG_R_PA
        return round((rate / LG_R_PA) * 100 / pf)

    t = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('team', '').strip().upper()
        if not tm: continue
        try:
            xwoba    = float(row.get('xwoba',     0) or 0)
            woba     = float(row.get('woba',      0) or 0) or xwoba
            bb_pct   = float(row.get('bb_pct',    0) or 0)
            hard_hit = float(row.get('hard_hit',  0) or 0)
            off      = _off(xwoba, bb_pct, hard_hit)
            t[tm] = {
                'xwoba':      xwoba,
                'off_score':  off,
                'woba':       woba,
                'hard_hit':   hard_hit,
                'bb_pct':     bb_pct,
                'barrel_pct': float(row.get('barrel_pct', 0) or 0),
                'avg_ev':     float(row.get('avg_ev',     0) or 0),
                'wrc_plus':   _wrc(woba, tm),
                'pa':         int(float(row.get('pa', 0) or 0)),
            }
        except: pass
    return t


def load_platoon(content):
    """Load team platoon splits (vs LHP / vs RHP) from team_platoon.csv."""
    p = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('team','').strip().upper()
        if not tm: continue
        try:
            p[tm] = {
                'off_vs_lhp': float(row.get('off_score_vs_lhp', 0) or 0),
                'off_vs_rhp': float(row.get('off_score_vs_rhp', 0) or 0),
                'xw_vs_lhp':  float(row.get('xwoba_vs_lhp',    0) or 0),
                'xw_vs_rhp':  float(row.get('xwoba_vs_rhp',    0) or 0),
                'pa_vs_lhp':  int(float(row.get('pa_vs_lhp', 0) or 0)),
                'pa_vs_rhp':  int(float(row.get('pa_vs_rhp', 0) or 0)),
            }
        except: pass
    return p


def load_pitcher_hand(content):
    """Load pitcher handedness cache (pitcher_id → 'L' or 'R')."""
    h = {}
    for row in csv.DictReader(io.StringIO(content)):
        pid = row.get('pitcher_id','').strip()
        if pid: h[pid] = row.get('hand','R').strip()
    return h


def team_off_score(team, sp_hand, teams, platoon):
    """
    Return the platoon-weighted offensive score for a team facing a given SP hand.
    If platoon data available and SP hand is known:
        70% vs-SP-hand split + 30% season average off_score
    Otherwise: season off_score.
    """
    base = teams.get(team, {})
    season_off = base.get('off_score') or base.get('xwoba', 0.318)

    if not sp_hand or not platoon:
        return season_off

    p = platoon.get(team, {})
    key = 'off_vs_lhp' if sp_hand == 'L' else 'off_vs_rhp'
    pa_key = 'pa_vs_lhp' if sp_hand == 'L' else 'pa_vs_rhp'

    vs_hand = p.get(key, 0)
    pa_split = p.get(pa_key, 0)

    # Only use platoon if we have meaningful sample (≥100 PA)
    if vs_hand and pa_split >= 100:
        return round(0.70 * vs_hand + 0.30 * season_off, 4)

    return season_off


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


def calc_off_score_matchup(team, team_data, platoon_data, sp_hand, full_game=True):
    """
    Offensive ability vs a specific SP handedness.
    Weights (full game):  35% wRC+_vs_hand, 30% xwOBA_vs_hand, 15% K/BB, 10% ISO/Barrel, 10% Pitch
    Weights (F5):         30% wRC+_vs_hand, 35% xwOBA_vs_hand, 13% K/BB,  8% ISO/Barrel, 14% Pitch

    Pitch-type matchup uses xwOBA vs hand as a proxy until SP arsenal data is available.
    Falls back to season off_score when platoon data has < 100 PA vs that handedness.
    """
    LG_XWOBA = 0.318; LG_WOBA_SCALE = 1.24; LG_R_PA = 0.119
    LG_BB = 8.5; LG_K = 22.0; LG_BARREL = 8.0
    PARK_FACTOR = {"COL":1.10,"BOS":1.05,"NYY":1.05,"CHC":1.03,"CIN":1.03,
                   "TEX":1.01,"HOU":1.01,"SDP":0.97,"SFG":0.97,"TBR":0.99,
                   "TOR":1.01,"MIN":1.01}

    w = ((0.35, 0.30, 0.15, 0.10, 0.10) if full_game else
         (0.30, 0.35, 0.13, 0.08, 0.14))

    # Resolve handedness-specific stats
    p         = platoon_data or {}
    hk        = 'lhp' if sp_hand == 'L' else 'rhp'
    xw_vh     = float(p.get(f'xwoba_vs_{hk}',  0) or 0)
    bb_vh     = float(p.get(f'bb_pct_vs_{hk}', 0) or 0)
    pa_vh     = int(float(p.get(f'pa_vs_{hk}', 0) or 0))

    # Fall back to season stats if platoon data is thin
    if pa_vh < 100 or xw_vh == 0:
        xw_vh = team_data.get('off_score') or team_data.get('xwoba', LG_XWOBA)
        bb_vh = team_data.get('bb_pct', LG_BB)

    # Season-level fallbacks for non-platoon components
    k_team  = team_data.get('k_pct',     LG_K)
    barrel  = team_data.get('barrel_pct', LG_BARREL)

    # C1: wRC+ vs handedness (normalized back to xwOBA scale)
    pf      = PARK_FACTOR.get(team, 1.00)
    rate    = (xw_vh - LG_XWOBA) / LG_WOBA_SCALE + LG_R_PA
    wrc_vh  = (rate / LG_R_PA) * 100 / pf
    wrc_comp = LG_XWOBA + (wrc_vh - 100) / 100 * LG_XWOBA

    # C2: xwOBA vs handedness
    xw_comp = xw_vh

    # C3: K%/BB% matchup — walks good (team on base), low K% good (makes contact)
    kbb_adj = (bb_vh - LG_BB) * 0.006 + (LG_K - k_team) * 0.003
    kbb_comp = LG_XWOBA + kbb_adj

    # C4: ISO/Barrel% — power production
    barrel_adj = (barrel - LG_BARREL) * 0.004
    iso_comp = LG_XWOBA + barrel_adj

    # C5: Pitch-type matchup (proxy = xwOBA vs hand until arsenal data is available)
    pitch_comp = xw_vh

    return round(w[0]*wrc_comp + w[1]*xw_comp + w[2]*kbb_comp +
                 w[3]*iso_comp + w[4]*pitch_comp, 4)


def calc_def_score(pitcher, bp_gap, bp_fat, park_team, f5=False):
    """
    Team run prevention composite. Expressed as expected xwOBA allowed.
    Lower = better defense. League average ≈ 0.318.

    Weights (full): 30% SP quality, 25% BP quality, 15% BP availability,
                    10% defense*, 10% batted-ball suppression, 5% catcher*, 5% park
    Weights (F5):   45% SP quality, 5% BP quality, 5% BP availability,
                    15% defense*, 15% batted-ball suppression, 10% catcher*, 5% park

    *Defense (OAA/DRS) and catcher (framing/CS) use neutral values until data available.

    Components where we have data:
      SP quality     — gap (regression), K%, BB%, hard_hit%
      BP quality     — bullpen gap
      BP availability — fatigue_score
      Batted-ball    — SP hard_hit%, barrel%
      Park           — PARK factor dict
    """
    LG_XWOBA = 0.318; LG_K = 22.0; LG_BB = 8.5; LG_HH = 37.0; LG_BAR = 8.0
    PARK_VALS = {"COL":-3.0,"BOS":-1.5,"NYY":-1.5,"CHC":-1.0,"CIN":-1.0,
                 "TEX":-0.5,"HOU":-0.5,"SDP":+1.5,"SFG":+1.5,"TBR":+0.5,
                 "TOR":+0.5,"MIN":+0.5}

    w = ((0.30, 0.25, 0.15, 0.10, 0.10, 0.05, 0.05) if not f5 else
         (0.45, 0.05, 0.05, 0.15, 0.15, 0.10, 0.05))

    sp_gap = pitcher.get('gap',      0.0) if pitcher else 0.0
    sp_k   = pitcher.get('k_pct',   LG_K) if pitcher else LG_K
    sp_bb  = pitcher.get('bb_pct',  LG_BB) if pitcher else LG_BB
    sp_hh  = pitcher.get('hard_hit',LG_HH) if pitcher else LG_HH
    sp_bar = pitcher.get('barrel_pct', LG_BAR) if pitcher else LG_BAR

    # C1: SP quality — higher K%, lower BB%, lower HH% → lower xwOBA allowed
    sp_qual  = ((sp_k  - LG_K)  * 0.005   # extra Ks reduce allowed xwOBA
              - (sp_bb - LG_BB) * 0.004   # extra BBs increase allowed xwOBA
              - (sp_hh - LG_HH) * 0.002)  # harder contact = more runs
    # Regression: negative gap = lucky SP = will allow more → higher def_score
    sp_regress = -sp_gap * 100 * 0.003
    sp_comp  = LG_XWOBA - sp_qual + sp_regress

    # C2: BP quality — positive bp_gap = unlucky pen = expects improvement = lower def
    bp_qual_comp = LG_XWOBA - bp_gap * 3.0

    # C3: BP availability — tired pen allows more runs
    bp_avail_comp = LG_XWOBA + (1.0 - bp_fat) * 0.025

    # C4: Defense — neutral (no OAA/DRS data yet)
    def_comp = LG_XWOBA

    # C5: Batted-ball suppression — SP hard_hit%, barrel%
    supp_adj  = (sp_hh - LG_HH) * 0.002 + (sp_bar - LG_BAR) * 0.002
    supp_comp = LG_XWOBA + supp_adj

    # C6: Catcher/run game — neutral (no framing/CS data yet)
    cat_comp = LG_XWOBA

    # C7: Park — positive PARK = pitcher-friendly = fewer runs allowed
    park_val  = PARK_VALS.get(park_team, 0.0)
    park_comp = LG_XWOBA - park_val * 0.005

    return round(w[0]*sp_comp  + w[1]*bp_qual_comp + w[2]*bp_avail_comp +
                 w[3]*def_comp + w[4]*supp_comp    + w[5]*cat_comp + w[6]*park_comp, 4)


def compute_composite(asn, hsn, at, ht, pitchers, teams, bullpen,
                      platoon=None, pitcher_hand=None,
                      away_pid=None, home_pid=None):
    a  = pitchers.get(asn)
    h  = pitchers.get(hsn)
    ab = teams.get(at)
    hb = teams.get(ht)
    ba = bullpen.get(at, {'gap':0,'fat':1.0})
    hb_bp = bullpen.get(ht, {'gap':0,'fat':1.0})

    # SP handedness from pitcher_hand cache (used for platoon weighting)
    away_hand = pitcher_hand.get(away_pid,'') if (pitcher_hand and away_pid) else ''
    home_hand = pitcher_hand.get(home_pid,'') if (pitcher_hand and home_pid) else ''

    sp = 0.0
    if a: sp += (a['gap'] * 100)
    if h: sp -= (h['gap'] * 100)

    # BAT: use off_score with platoon weighting
    # Away team faces home SP (home_hand); home team faces away SP (away_hand)
    away_off = team_off_score(at, home_hand, teams, platoon)
    home_off = team_off_score(ht, away_hand, teams, platoon)
    bat = round((away_off - home_off) * 100, 2)

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

    if miss:
        rec['f5'] = rec['full'] = rec['run_line'] = False

    # Determine platoon flag for display
    platoon_active = bool(away_hand or home_hand)

    # ── Matchup scores ────────────────────────────────────────────────────────
    # Away team faces home SP (home_hand); home team faces away SP (away_hand)
    a_off_mu    = calc_off_score_matchup(at, ab or {}, platoon.get(at, {}) if platoon else {},
                                         home_hand, full_game=True)
    h_off_mu    = calc_off_score_matchup(ht, hb or {}, platoon.get(ht, {}) if platoon else {},
                                         away_hand, full_game=True)
    a_off_mu_f5 = calc_off_score_matchup(at, ab or {}, platoon.get(at, {}) if platoon else {},
                                         home_hand, full_game=False)
    h_off_mu_f5 = calc_off_score_matchup(ht, hb or {}, platoon.get(ht, {}) if platoon else {},
                                         away_hand, full_game=False)

    # def_score uses the OPPOSING pitcher (what they'll face)
    # Away def = home pitcher quality vs away batters
    # Home def = away pitcher quality vs home batters
    a_def    = calc_def_score(h,   ba['gap'],  ba['fat'],  at, f5=False)
    h_def    = calc_def_score(a,   hb_bp['gap'],  hb_bp['fat'],  ht, f5=False)
    a_def_f5 = calc_def_score(h,   ba['gap'],  ba['fat'],  at, f5=True)
    h_def_f5 = calc_def_score(a,   hb_bp['gap'],  hb_bp['fat'],  ht, f5=True)

    # ── Individual SP components (stored separately for correlation analysis) ──
    LG_K = 22.0; LG_HH = 37.0; LG_BAR = 8.0; LG_BB = 8.5

    def _sp(pitcher, field, default):
        return round(pitcher.get(field, default), 2) if pitcher else round(default, 2)

    a_k   = _sp(a, 'k_pct',      LG_K)
    a_bb  = _sp(a, 'bb_pct',     LG_BB)
    a_hh  = _sp(a, 'hard_hit',   LG_HH)
    a_bar = _sp(a, 'barrel_pct', LG_BAR)
    h_k   = _sp(h, 'k_pct',      LG_K)
    h_bb  = _sp(h, 'bb_pct',     LG_BB)
    h_hh  = _sp(h, 'hard_hit',   LG_HH)
    h_bar = _sp(h, 'barrel_pct', LG_BAR)

    at_k   = round(ab.get('k_pct',     LG_K)   if ab else LG_K,   2)
    at_bar = round(ab.get('barrel_pct',LG_BAR) if ab else LG_BAR, 2)
    ht_k   = round(hb.get('k_pct',     LG_K)   if hb else LG_K,   2)
    ht_bar = round(hb.get('barrel_pct',LG_BAR) if hb else LG_BAR, 2)

    # K% matchup: SP K% advantage over opposing lineup's typical K rate
    # Positive = SP dominates that lineup at the plate (harder to make contact)
    k_mu_away = round(a_k - ht_k, 2)   # away SP K% vs home lineup K%
    k_mu_home = round(h_k - at_k, 2)   # home SP K% vs away lineup K%

    # HH% matchup: team HH% produced minus SP HH% allowed
    # Positive = offense hits harder than SP suppresses (offense advantage)
    hh_mu_away = round(ht_k - a_hh, 2)   # home offense HH% vs away SP HH% allowed
    hh_mu_home = round(at_k - h_hh, 2)   # away offense HH% vs home SP HH% allowed
    # Note: using team k_pct as a proxy for hard contact production until
    # team hard_hit% is fully populated from statcast_batting.csv aggregation

    return {
        'sp_edge': round(sp,2), 'bat_edge': round(bat,2),
        'bp_edge': bp, 'park_adj': park, 'composite': adj,
        'band': band, 'model_dir': model,
        'aligned': aln, 'alignment_type': 'BILATERAL' if std else ('SP-DOM' if spd else 'NONE'),
        'qualified': qual,
        'away_gap': a['gap'] if a else '',
        'home_gap': h['gap'] if h else '',
        'away_off_score': round(away_off, 4),
        'home_off_score': round(home_off, 4),
        'away_wrc_plus':  ab.get('wrc_plus', '') if ab else '',
        'home_wrc_plus':  hb.get('wrc_plus', '') if hb else '',
        'away_sp_hand': home_hand,
        'home_sp_hand': away_hand,
        'away_off_score_matchup':    a_off_mu,
        'home_off_score_matchup':    h_off_mu,
        'away_off_score_matchup_f5': a_off_mu_f5,
        'home_off_score_matchup_f5': h_off_mu_f5,
        'away_def_score':    a_def,
        'home_def_score':    h_def,
        'away_def_score_f5': a_def_f5,
        'home_def_score_f5': h_def_f5,
        'away_sp_k_pct':  a_k,   'home_sp_k_pct':  h_k,
        'away_sp_bb_pct': a_bb,  'home_sp_bb_pct': h_bb,
        'away_sp_hard_hit':a_hh, 'home_sp_hard_hit':h_hh,
        'away_sp_barrel': a_bar, 'home_sp_barrel': h_bar,
        'away_sp_kbb': round(a_k - a_bb, 2), 'home_sp_kbb': round(h_k - h_bb, 2),
        'away_team_k_pct':  at_k,  'home_team_k_pct':  ht_k,
        'away_team_barrel': at_bar, 'home_team_barrel': ht_bar,
        'k_pct_matchup_away': k_mu_away, 'k_pct_matchup_home': k_mu_home,
        'hh_matchup_away': hh_mu_away,  'hh_matchup_home': hh_mu_home,
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
    platoon_content = fetch_optional('team_platoon.csv', 'team_platoon.csv')
    hand_content    = fetch_optional('pitcher_hand.csv', 'pitcher_hand.csv')

    # Merge bullpen + fatigue (empty dicts if files missing)
    bp_raw  = {r['team']: r for r in csv.DictReader(io.StringIO(bullpen_content))} \
              if bullpen_content else {}
    fat_raw = {r['team']: r for r in csv.DictReader(io.StringIO(fatigue_content))} \
              if fatigue_content else {}
    bullpen = {}
    for tm in set(list(bp_raw.keys()) + list(fat_raw.keys())):
        gap   = float(bp_raw.get(tm,{}).get('bullpen_gap',0) or 0)
        fat   = float(fat_raw.get(tm,{}).get('fatigue_score',1.0) or 1.0)
        tired = int(float(fat_raw.get(tm,{}).get('arms_tired',0) or 0))
        bullpen[tm] = {'gap': gap, 'fat': fat, 'tired': tired}

    pitchers     = load_pitchers(stats_content)
    teams        = load_teams(sc_content)    if sc_content      else {}
    platoon      = load_platoon(platoon_content) if platoon_content else {}
    pitcher_hand = load_pitcher_hand(hand_content) if hand_content else {}

    # Games
    games = []
    for row in csv.DictReader(io.StringIO(pp_content)):
        at = NORM.get(row['away_team'], row['away_team'])
        ht = NORM.get(row['home_team'], row['home_team'])
        games.append({
            'at': at, 'ht': ht,
            'asp': row.get('away_pitcher','TBD'),
            'hsp': row.get('home_pitcher','TBD'),
            'away_pid': row.get('away_pitcher_id','').strip(),
            'home_pid': row.get('home_pitcher_id','').strip(),
            'game_date': row.get('game_date', game_date),
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

        c = compute_composite(
            asn, hsn, at, ht, pitchers, teams, bullpen,
            platoon=platoon, pitcher_hand=pitcher_hand,
            away_pid=g.get('away_pid',''), home_pid=g.get('home_pid',''),
        )

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
            'away_off_score': c['away_off_score'],
            'home_off_score': c['home_off_score'],
            'away_wrc_plus':  c['away_wrc_plus'],
            'home_wrc_plus':  c['home_wrc_plus'],
            'away_sp_hand':   c['away_sp_hand'],
            'home_sp_hand':   c['home_sp_hand'],
            'away_off_score_matchup':    c['away_off_score_matchup'],
            'home_off_score_matchup':    c['home_off_score_matchup'],
            'away_off_score_matchup_f5': c['away_off_score_matchup_f5'],
            'home_off_score_matchup_f5': c['home_off_score_matchup_f5'],
            'away_def_score':    c['away_def_score'],
            'home_def_score':    c['home_def_score'],
            'away_def_score_f5': c['away_def_score_f5'],
            'home_def_score_f5': c['home_def_score_f5'],
            'away_sp_k_pct':  c['away_sp_k_pct'],  'home_sp_k_pct':  c['home_sp_k_pct'],
            'away_sp_bb_pct': c['away_sp_bb_pct'], 'home_sp_bb_pct': c['home_sp_bb_pct'],
            'away_sp_hard_hit':c['away_sp_hard_hit'],'home_sp_hard_hit':c['home_sp_hard_hit'],
            'away_sp_barrel': c['away_sp_barrel'], 'home_sp_barrel': c['home_sp_barrel'],
            'away_sp_kbb':    c['away_sp_kbb'],    'home_sp_kbb':    c['home_sp_kbb'],
            'away_team_k_pct':  c['away_team_k_pct'], 'home_team_k_pct':  c['home_team_k_pct'],
            'away_team_barrel': c['away_team_barrel'],'home_team_barrel': c['home_team_barrel'],
            'k_pct_matchup_away': c['k_pct_matchup_away'],
            'k_pct_matchup_home': c['k_pct_matchup_home'],
            'hh_matchup_away': c['hh_matchup_away'],
            'hh_matchup_home': c['hh_matchup_home'],
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
