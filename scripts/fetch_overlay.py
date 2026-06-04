#!/usr/bin/env python3
"""
fetch_overlay.py
================
Automatically runs the Professional Overlay Score (POS) for today's
qualifying plays. No manual input required.

Pulls from the data pipeline (stats, bullpen, fatigue, odds) + uses the
Claude API with web search to research each SP's xERA/FIP, recent form,
injuries, and lineup context.

Usage:
    python scripts/fetch_overlay.py              # today's qualifying plays
    python scripts/fetch_overlay.py --all        # all games (not just qualifying)
    python scripts/fetch_overlay.py --min-adj 3  # custom composite threshold

Output:
    data/overlay_YYYYMMDD.json   full overlay data
    data/overlay_YYYYMMDD.txt    human-readable bet cards
    Prints a complete bet card to stdout

Requires ANTHROPIC_API_KEY in environment (already set in GitHub Actions
via secrets if you add it).
"""

import csv, io, json, os, sys, time, urllib.request, urllib.error
from datetime import date, datetime
from pathlib import Path

DATA_DIR   = Path("data")
TIMEOUT    = 30
YEAR       = 2026

NORM = {'TB':'TBR','KC':'KCR','SD':'SDP','SF':'SFG','AZ':'ARI'}
PARK = {'COL':-3.0,'BOS':-1.5,'NYY':-1.5,'CHC':-1.0,'CIN':-1.0,
        'TEX':-0.5,'HOU':-0.5,'SDP':+1.5,'SFG':+1.5,'TBR':+0.5,'TOR':+0.5,'MIN':+0.5}

GITHUB_RAW = "https://raw.githubusercontent.com/johnnydoe314/mlb-data/main/data"

# ─────────────────────────────────────────────────────────────────────────────
# POS rubric (sent to Claude as system context)
# ─────────────────────────────────────────────────────────────────────────────
POS_SYSTEM = """You are an elite MLB sports bettor. You receive structured model output
for an MLB game and must research it thoroughly using web search, then produce a
Professional Overlay Score (POS) and bet card.

RESEARCH REQUIRED (use web_search for each):
1. Both SP's xERA, FIP, xFIP, K-BB%, Stuff+ for the current season
2. Both SP's last 3 starts (ERA, K, BB, IP trend — improving or deteriorating?)
3. Both teams' injury/lineup news for today
4. Any relevant head-to-head context (same pitchers recently? series context?)
5. Bullpen quality context if model flags fatigue concern

POS SCORING (-12 to +12):
  SP Quality (xERA/FIP confirms or contradicts model gap signal): -4 to +4
    +3/+4 = xERA/FIP strongly agrees with model's lucky/unlucky call
    0/+1  = xERA/FIP roughly neutral or mixed signals
    -3/-4 = xERA/FIP directly contradicts model (e.g. model says lucky but xFIP says genuinely great)

  Injury/Lineup impact: -4 to +4
    +2 = key opposing lineup player out, favoring our side
    -2 = key player on our side out
    +4 = multiple stars missing from opponent

  Recent form (last 3 starts trend): -2 to +2
    +2 = SP on our side has been dominant in last 3, improving trend
    -2 = SP on our side has been deteriorating

  Bullpen fatigue/quality: -2 to +2
    (Already calculated from bullpen_fatigue.csv — just confirm/adjust)

  Line value: -2 to +2
    +2 = F5 available and cleanly captures SP edge, great price
    +1 = ML/RL at good value given composite
    -1 = price is too steep, edge disappears at this price
    -2 = line has moved against us significantly

  Park/weather supplement: -1 to +1
    Additional context beyond the fixed park adjustment already in composite

POS INTERPRETATION:
  > +4  = Strong confirmation → full unit bet
  +1–4  = Confirmation → standard unit
  -1–1  = Marginal → F5 only or reduce size
  < -2  = Override → pass or fade

BET STRUCTURE RULES:
- SP-dominant plays with depleted bullpen (fatigue < 0.65) → ALWAYS recommend F5, not full game
- 8+ bilateral → 1.0u primary
- 5-8 bilateral → 0.75u primary
- 5-8 SP-dominant → 0.60u primary (higher variance)
- 2-5 any → 0.40u max, F5 preferred
- ML price threshold: never recommend ML above -175 as primary; prefer RL or F5

RESPOND WITH ONLY VALID JSON. No preamble. No markdown. Just the JSON object:
{
  "game": "AWAY @ HOME",
  "composite": number,
  "band": "8+ | 5-8 | 2-5",
  "model_direction": "AWAY | HOME",
  "alignment": "BILATERAL | SP-DOM",
  "pos_components": {
    "sp_quality":    {"score": int, "rationale": "one sentence"},
    "injury_lineup": {"score": int, "rationale": "one sentence"},
    "recent_form":   {"score": int, "rationale": "one sentence"},
    "bullpen":       {"score": int, "rationale": "one sentence"},
    "line_value":    {"score": int, "rationale": "one sentence"},
    "park_weather":  {"score": int, "rationale": "one sentence"}
  },
  "pos_total": int,
  "verdict": "STRONG PLAY | PLAY | MARGINAL | PASS | FADE",
  "primary_bet": "exact bet description e.g. ATL -1.5 (+139)",
  "secondary_bet": "backup or null",
  "units": number,
  "price_threshold": "e.g. ML no worse than -155",
  "f5_preferred": boolean,
  "alarm_bells": ["list of specific concerns"],
  "key_edge": "one sentence on the core edge"
}"""


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders (same as log_games.py)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_raw(path):
    url = f"{GITHUB_RAW}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "fetch_overlay/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")


def load_pitchers(content):
    p = {}
    reader = csv.reader(io.StringIO(content))
    hdrs = [h.strip().strip('"') for h in next(reader)]
    for row in reader:
        d = dict(zip(hdrs, row))
        if d.get('year','').strip() != str(YEAR): continue
        name = d.get('last_name, first_name','').strip()
        if not name: continue
        try:
            pa = int(d.get('pa',0) or 0)
            if name in p and pa <= p[name]['pa']: continue
            p[name] = {
                'pa':       pa,
                'woba':     float(d.get('woba',0) or 0),
                'xwoba':    float(d.get('xwoba',0) or 0),
                'gap':      round(float(d.get('woba',0) or 0) - float(d.get('xwoba',0) or 0), 3),
                'k_pct':    float(d.get('k_percent',0) or 0),
                'bb_pct':   float(d.get('bb_percent',0) or 0),
                'hh':       float(d.get('hard_hit_percent',0) or 0),
                'ip':       d.get('p_formatted_ip',''),
            }
        except: pass
    return p


def load_teams(content):
    t = {}
    for row in csv.DictReader(io.StringIO(content)):
        tm = row.get('Team','').strip().upper()
        if not tm or tm == 'MLB': continue
        try:
            t[tm] = {
                'xwoba':  float(row.get('xwOBA',0)),
                'barrel': float(row.get('Barrel%',0)),
                'hh':     float(row.get('Hard Hit%',0)),
            }
        except: pass
    return t


def load_bullpen_merged(bp_content, fat_content):
    bp  = {r['team']: r for r in csv.DictReader(io.StringIO(bp_content))}
    fat = {r['team']: r for r in csv.DictReader(io.StringIO(fat_content))}
    merged = {}
    for tm in set(list(bp.keys()) + list(fat.keys())):
        merged[tm] = {
            'gap':    float(bp.get(tm,{}).get('bullpen_gap',0) or 0),
            'fat':    float(fat.get(tm,{}).get('fatigue_score',1.0) or 1.0),
            'tired':  int(fat.get(tm,{}).get('arms_tired',0) or 0),
            'rps':    int(bp.get(tm,{}).get('pitchers_counted',0) or 0),
        }
    return merged


def compute(asn, hsn, at, ht, pitchers, teams, bullpen):
    a  = pitchers.get(asn); h  = pitchers.get(hsn)
    ab = teams.get(at);     hb = teams.get(ht)
    ba = bullpen.get(at,{}); hb_bp = bullpen.get(ht,{})

    sp = 0.0
    if a: sp += (a['gap'] * 100)
    if h: sp -= (h['gap'] * 100)

    bat = (ab['xwoba'] - hb['xwoba']) * 100 if ab and hb else 0.0

    bg  = ba.get('gap',0) * ba.get('fat',1.0)
    hg  = hb_bp.get('gap',0) * hb_bp.get('fat',1.0)
    bp_edge = round((bg - hg) * -50, 2) if ba and hb_bp else 0.0

    park = PARK.get(ht, 0)
    raw  = round(sp + bat + bp_edge, 2)
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
        'bp_edge': bp_edge, 'park': park, 'composite': adj,
        'band': band, 'model': model, 'std': std, 'spd': spd,
        'aligned': aln, 'qualified': qual, 'missing_sp': miss,
        'away_sp_data': a, 'home_sp_data': h,
        'away_bat': ab, 'home_bat': hb,
        'away_bp': ba, 'home_bp': hb_bp,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Claude API call with web search
# ─────────────────────────────────────────────────────────────────────────────

def call_claude(api_key, prompt_text):
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model":      "claude-sonnet-4-20250514",
        "max_tokens": 1500,
        "system":     POS_SYSTEM,
        "tools":      [{"type": "web_search_20250305", "name": "web_search"}],
        "messages":   [{"role": "user", "content": prompt_text}],
    }
    headers = {
        "Content-Type":         "application/json",
        "x-api-key":            api_key,
        "anthropic-version":    "2023-06-01",
        "anthropic-beta":       "web-search-2025-03-05",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def extract_json(response_data):
    text = "".join(
        b.get("text","") for b in response_data.get("content",[])
        if b.get("type") == "text"
    )
    # Strip markdown fences
    text = text.replace("```json","").replace("```","").strip()
    # Find outermost JSON object
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON found in response: {text[:200]}")
    return json.loads(text[start:end])


def build_prompt(g, c, o):
    at, ht = g['at'], g['ht']
    asn, hsn = g['asp'], g['hsp']
    a  = c['away_sp_data']; h  = c['home_sp_data']
    ab = c['away_bat'];     hb = c['home_bat']
    ba = c['away_bp'];      hb_bp = c['home_bp']

    today = date.today().strftime("%B %d, %Y")
    direction = c['model']
    bet_team  = at if direction == 'AWAY' else ht
    bet_sp    = asn if direction == 'AWAY' else hsn
    aln_str   = 'BILATERAL' if c['std'] else ('SP-DOMINANT' if c['spd'] else 'OTHER')

    # SP summaries
    def sp_str(name, data, side):
        if not data:
            return f"{side} SP {name}: not in stats file (missing data)"
        luck = "LUCKY→will regress" if data['gap']<-0.015 else \
               ("UNLUCKY→will improve" if data['gap']>0.015 else "neutral")
        return (f"{side} SP {name}: wOBA-xwOBA gap {data['gap']:+.3f} ({luck}), "
                f"K%:{data['k_pct']:.1f}, BB%:{data['bb_pct']:.1f}, "
                f"HH%:{data['hh']:.1f}, IP this season:{data['ip']}")

    def bat_str(team, data):
        if not data: return f"{team}: no batting data"
        return f"{team}: xwOBA {data['xwoba']:.3f}, Barrel%:{data['barrel']:.1f}, HH%:{data['hh']:.1f}"

    def bp_str(team, data):
        if not data: return f"{team} bullpen: no data"
        fat_flag = " 🔴 DEPLETED" if data.get('fat',1)<0.65 else \
                   (" 🟡 TIRED" if data.get('fat',1)<0.80 else " 🟢 FRESH")
        return (f"{team} bullpen: gap {data.get('gap',0):+.4f}, "
                f"fatigue {data.get('fat',1):.3f}{fat_flag}, "
                f"{data.get('tired',0)} tired arms, {data.get('rps',0)} RPs tracked")

    ml_str = (f"{at} ML:{o.get('away_ml','?')}, {ht} ML:{o.get('home_ml','?')}, "
              f"RL:{o.get('away_rl','?')}/{o.get('home_rl','?')}, "
              f"O/U:{o.get('total','?')}")

    return f"""Today is {today}. Research and score this qualifying MLB play.

GAME: {at} (away) @ {ht} (home)
MODEL DIRECTION: {direction} ({bet_team}) — {aln_str}
COMPOSITE: {c['composite']:+.1f} ({c['band']} band)
  SP edge:  {c['sp_edge']:+.2f}
  BAT edge: {c['bat_edge']:+.2f}
  BP edge:  {c['bp_edge']:+.2f}
  Park adj: {c['park']:+.1f}

STARTING PITCHERS:
  {sp_str(asn, a, 'Away')}
  {sp_str(hsn, h, 'Home')}

TEAM BATTING:
  {bat_str(at, ab)}
  {bat_str(ht, hb)}

BULLPEN:
  {bp_str(at, ba)}
  {bp_str(ht, hb_bp)}

LINES: {ml_str}

Search for: xERA/FIP/Stuff+ for both {asn} and {hsn} this season, their last 3 starts,
injury/lineup news for {at} and {ht} today, any relevant series/H2H context.
Then score the POS components and produce the bet card JSON."""


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_bet_card(overlay, game_info):
    pos = overlay.get('pos_total', 0)
    verdict = overlay.get('verdict','?')
    verdict_color = {
        'STRONG PLAY': '★★★',
        'PLAY':        '★★',
        'MARGINAL':    '★',
        'PASS':        '—',
        'FADE':        '↓',
    }.get(verdict, '?')

    comp = overlay.get('pos_components',{})
    lines = [
        f"\n{'═'*66}",
        f"  {overlay.get('game','?')}  │  {overlay.get('composite',0):+.1f} "
        f"({overlay.get('band','?')})  │  {overlay.get('model_direction','?')}  │  "
        f"{overlay.get('alignment','?')}",
        f"{'─'*66}",
        f"  PROFESSIONAL OVERLAY SCORE: {pos:+d}  →  {verdict} {verdict_color}",
        f"{'─'*66}",
    ]

    # POS components
    for key, label, maxv in [
        ('sp_quality',    'SP Quality (xERA/FIP)',       4),
        ('injury_lineup', 'Injury / Lineup',             4),
        ('recent_form',   'Recent Form',                 2),
        ('bullpen',       'Bullpen',                     2),
        ('line_value',    'Line Value',                  2),
        ('park_weather',  'Park / Weather',              1),
    ]:
        c = comp.get(key,{})
        sc = c.get('score',0)
        bar = ('▓' * abs(sc)) + ('░' * (maxv - abs(sc)))
        sign = '+' if sc >= 0 else ''
        lines.append(f"  {label:<26} {sign}{sc:>+3}  {bar}  {c.get('rationale','')}")

    lines += [
        f"{'─'*66}",
        f"  KEY EDGE:  {overlay.get('key_edge','')}",
    ]

    bells = overlay.get('alarm_bells',[])
    if bells:
        lines.append(f"  ALARM BELLS:")
        for b in bells:
            lines.append(f"    ⚠ {b}")

    lines += [
        f"{'─'*66}",
        f"  BET CARD",
        f"    Primary:   {overlay.get('primary_bet','?')}  ({overlay.get('units',0)}u)",
    ]
    sec = overlay.get('secondary_bet')
    if sec and sec not in ('null', None, ''):
        lines.append(f"    Secondary: {sec}")
    lines.append(f"    Max price: {overlay.get('price_threshold','?')}")
    if overlay.get('f5_preferred'):
        lines.append(f"    ⚡ F5 PREFERRED — bullpen flag active")
    lines.append(f"{'═'*66}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Automated professional overlay for qualifying plays')
    parser.add_argument('--all',      action='store_true', help='Run overlay on all games, not just qualifying')
    parser.add_argument('--min-adj',  type=float, default=5.0, help='Min |composite| to include (default 5.0)')
    parser.add_argument('--date',     default=date.today().isoformat())
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        print("[!] ANTHROPIC_API_KEY not set — cannot run overlay.")
        sys.exit(1)

    today     = args.date
    ts        = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    DATA_DIR.mkdir(exist_ok=True)

    print(f"{'='*60}")
    print(f"  PROFESSIONAL OVERLAY — {ts}")
    print(f"{'='*60}")

    # Load all data
    print("\n  Loading data...", end="", flush=True)
    try:
        stats    = fetch_raw('stats.csv')
        pp       = fetch_raw('probable_pitchers.csv')
        sc       = fetch_raw('statcast_hitting_2026.csv')
        odds_raw = fetch_raw('odds.csv')
        bp_raw   = fetch_raw('bullpen.csv')
        fat_raw  = fetch_raw('bullpen_fatigue.csv')
    except Exception as e:
        print(f"\n  [!] {e}")
        sys.exit(1)

    pitchers = load_pitchers(stats)
    teams    = load_teams(sc)
    bullpen  = load_bullpen_merged(bp_raw, fat_raw)
    odds_map = {}
    for r in csv.DictReader(io.StringIO(odds_raw)):
        at = NORM.get(r['away_team'],r['away_team'])
        ht = NORM.get(r['home_team'],r['home_team'])
        odds_map[(at,ht)] = r

    games = []
    for row in csv.DictReader(io.StringIO(pp)):
        at = NORM.get(row['away_team'],row['away_team'])
        ht = NORM.get(row['home_team'],row['home_team'])
        games.append({'at':at,'ht':ht,'asp':row.get('away_pitcher','TBD'),'hsp':row.get('home_pitcher','TBD')})

    print(f" {len(pitchers)} pitchers | {len(games)} games")

    # Compute composites, filter to target plays
    target_games = []
    for g in games:
        c = compute(g['asp'], g['hsp'], g['at'], g['ht'], pitchers, teams, bullpen)
        g['composite'] = c
        if args.all or abs(c['composite']) >= args.min_adj:
            if args.all or c['qualified']:
                target_games.append(g)

    if not target_games:
        print(f"\n  No qualifying plays today (min |adj| {args.min_adj}).")
        print("  Run with --all to overlay all games, or --min-adj 3 for near misses.")
        return

    print(f"\n  Running overlay on {len(target_games)} play(s)...\n")

    overlays  = []
    out_lines = []

    for g in target_games:
        at,ht  = g['at'], g['ht']
        c      = g['composite']
        o      = odds_map.get((at,ht), {})

        print(f"  {at}@{ht} (composite {c['composite']:+.1f})... ", end="", flush=True)

        prompt = build_prompt(g, c, o)
        try:
            response = call_claude(api_key, prompt)
            overlay  = extract_json(response)
            overlay['_game']  = f"{at}@{ht}"
            overlay['_model'] = c
            overlays.append(overlay)
            card = format_bet_card(overlay, g)
            out_lines.append(card)
            print(f"POS {overlay.get('pos_total',0):+d} → {overlay.get('verdict','?')}")
            print(card)
        except Exception as e:
            print(f"ERROR: {e}")
            overlays.append({'_game':f"{at}@{ht}", 'error': str(e)})
        time.sleep(1)  # brief pause between API calls

    # Save outputs
    json_out = DATA_DIR / f"overlay_{today.replace('-','')}.json"
    txt_out  = DATA_DIR / f"overlay_{today.replace('-','')}.txt"

    json_out.write_text(json.dumps(overlays, indent=2))
    txt_out.write_text("\n".join(out_lines))

    print(f"\n  [✓] {json_out}")
    print(f"  [✓] {txt_out}")


if __name__ == "__main__":
    main()
