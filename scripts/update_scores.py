#!/usr/bin/env python3
"""
update_scores.py — fetch final scores AND F5/starter-exit/inning data
from the MLB Stats API live game feed, updating game_log.csv in one pass.

Flow:
  1. schedule endpoint       → get gamePks for target date
  2. /game/{pk}/feed/live   → linescore + boxscore per game
  3. Derive:
       away_score, home_score
       away_f5, home_f5, f5_total, f5_result
       away_innings, home_innings   (comma-separated per-inning runs, all innings)
       away_sp_exit_inn             (inning away starter last pitched in)
       away_sp_exit_score           ("away_runs-home_runs" at end of that inning)
       home_sp_exit_inn, home_sp_exit_score  (same for home starter)
  4. Compute: lean, model, f5_lean, f5_correct → write to game_log.csv
"""

import csv
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta
from pathlib import Path

MLB_SCH  = "https://statsapi.mlb.com/api/v1/schedule"
MLB_FEED = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
LOG_FILE = Path(os.environ.get("LOG_FILE", "data/game_log.csv"))
TIMEOUT  = 25

MLB_NORM = {
    "AZ": "ARI", "KC": "KCR", "SD": "SDP", "SF": "SFG",
    "TB": "TBR", "OAK": "ATH", "LAN": "LAD",
}
def norm(t: str) -> str:
    return MLB_NORM.get(t.strip().upper(), t.strip().upper())


# ── MLB API helpers ───────────────────────────────────────────────────────────

def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "update_scores/3.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def get_gamepks(target_date: str) -> list[int]:
    """Return list of regular-season gamePks for a date."""
    url = f"{MLB_SCH}?sportId=1&date={target_date}&gameType=R"
    data = _get(url)
    pks = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            pks.append(g["gamePk"])
    return pks


# ── Starter exit helpers ──────────────────────────────────────────────────────

def _ip_to_exit_inn(ip_str: str) -> int | None:
    """
    Convert inningsPitched string ('4.1', '6.0', '5.2') to the last inning
    the starter appeared in.
      '4.1' → 5  (got 1 out in the 5th before being removed)
      '5.0' → 5  (completed the 5th, not needed in 6th)
      '6.2' → 7  (got 2 outs in the 7th before being removed)
    """
    try:
        parts = ip_str.split(".")
        full = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return full + 1 if outs > 0 else full
    except Exception:
        return None


def _starter_exit(boxscore_team: dict, innings: list) -> tuple[int | None, str | None]:
    """
    Returns (exit_inn, score_str) for the starting pitcher.
      exit_inn  : last inning they appeared in (1-indexed)
      score_str : 'away_runs-home_runs' at the end of their last COMPLETE inning
    """
    pitchers = boxscore_team.get("pitchers", [])
    if not pitchers:
        return None, None

    starter_id  = pitchers[0]
    player_key  = f"ID{starter_id}"
    players     = boxscore_team.get("players", {})
    starter_p   = players.get(player_key, {})
    ip_str      = (starter_p
                   .get("stats", {})
                   .get("pitching", {})
                   .get("inningsPitched", ""))
    if not ip_str:
        return None, None

    exit_inn = _ip_to_exit_inn(ip_str)
    if exit_inn is None:
        return None, None

    # Score at end of the last COMPLETE inning the starter pitched through.
    # full_innings = floor of IP; that's how many complete innings they finished.
    try:
        full_innings = int(ip_str.split(".")[0])
    except Exception:
        return exit_inn, None

    # Sum runs in innings 1..full_innings from the sorted innings array
    inn_sorted = sorted(innings, key=lambda x: int(x.get("num", 0)))
    away_runs  = sum(int(i.get("away", {}).get("runs") or 0)
                     for i in inn_sorted if int(i.get("num", 0)) <= full_innings)
    home_runs  = sum(int(i.get("home", {}).get("runs") or 0)
                     for i in inn_sorted if int(i.get("num", 0)) <= full_innings)

    return exit_inn, f"{away_runs}-{home_runs}"


# ── Main game-result fetcher ──────────────────────────────────────────────────

def get_game_result(pk: int) -> dict | None:
    """
    Fetch live feed for a single game.
    Returns None if the game isn't Final yet.
    Returns dict with all derived fields.
    """
    url = MLB_FEED.format(pk=pk)
    try:
        data = _get(url)
    except Exception as e:
        print(f"    [!] gamePk {pk} — fetch error: {e}", file=sys.stderr)
        return None

    gd = data.get("gameData", {})
    ld = data.get("liveData", {})
    ls = ld.get("linescore", {})
    bs = ld.get("boxscore", {})

    # Only process completed games
    if gd.get("status", {}).get("abstractGameState", "") != "Final":
        return None

    away_abbrev = norm(gd.get("teams", {}).get("away", {}).get("abbreviation", ""))
    home_abbrev = norm(gd.get("teams", {}).get("home", {}).get("abbreviation", ""))
    game_date   = gd.get("datetime", {}).get("officialDate", "")

    # ── Final scores ──────────────────────────────────────────────────────────
    ls_teams   = ls.get("teams", {})
    away_score = int(ls_teams.get("away", {}).get("runs") or 0)
    home_score = int(ls_teams.get("home", {}).get("runs") or 0)

    # ── Per-inning data ───────────────────────────────────────────────────────
    innings = ls.get("innings", [])
    inn_sorted = sorted(innings, key=lambda x: int(x.get("num", 0)))

    # F5 — sum runs in innings 1-5
    away_f5 = sum(int(i.get("away", {}).get("runs") or 0)
                  for i in inn_sorted if int(i.get("num", 0)) <= 5)
    home_f5 = sum(int(i.get("home", {}).get("runs") or 0)
                  for i in inn_sorted if int(i.get("num", 0)) <= 5)

    f5_total  = away_f5 + home_f5
    f5_result = ("AWAY" if away_f5 > home_f5
                 else "HOME" if home_f5 > away_f5
                 else "Tie")

    # Per-inning strings — comma-separated runs for each inning played
    # e.g. away_innings = "0,1,0,2,0,0,1,0,0"
    def _inn_str(side: str) -> str:
        return ",".join(
            str(int(i.get(side, {}).get("runs") or 0))
            for i in inn_sorted
        )
    away_innings = _inn_str("away")
    home_innings = _inn_str("home")

    # ── Starter exit data ─────────────────────────────────────────────────────
    bs_teams = bs.get("teams", {})
    away_sp_exit_inn, away_sp_exit_score = _starter_exit(
        bs_teams.get("away", {}), inn_sorted)
    home_sp_exit_inn, home_sp_exit_score = _starter_exit(
        bs_teams.get("home", {}), inn_sorted)

    return dict(
        game_date          = game_date,
        away               = away_abbrev,
        home               = home_abbrev,
        away_score         = away_score,
        home_score         = home_score,
        away_f5            = away_f5,
        home_f5            = home_f5,
        f5_total           = f5_total,
        f5_result          = f5_result,
        away_innings       = away_innings,
        home_innings       = home_innings,
        away_sp_exit_inn   = away_sp_exit_inn,
        away_sp_exit_score = away_sp_exit_score,
        home_sp_exit_inn   = home_sp_exit_inn,
        home_sp_exit_score = home_sp_exit_score,
    )


# ── Derived-field helpers ─────────────────────────────────────────────────────

def _lean(composite, a_sc, h_sc) -> str | int:
    try:
        adj = float(composite or 0)
    except (TypeError, ValueError):
        return ""
    if abs(adj) < 0.05 or a_sc == h_sc:
        return ""
    model  = "AWAY" if adj > 0 else "HOME"
    actual = "AWAY" if a_sc > h_sc else "HOME"
    return 1 if model == actual else 0


def _model(composite, model_dir, qualified, a_sc, h_sc) -> str | int:
    try:
        if int(qualified or 0) != 1:
            return ""
    except (TypeError, ValueError):
        return ""
    if not model_dir or model_dir == "NEUT" or a_sc == h_sc:
        return ""
    actual = "AWAY" if a_sc > h_sc else "HOME"
    return 1 if model_dir == actual else 0


def _f5_lean(composite, f5_result) -> str | int:
    if not f5_result or f5_result == "Tie":
        return ""
    try:
        adj = float(composite or 0)
    except (TypeError, ValueError):
        return ""
    if abs(adj) < 0.05:
        return ""
    model = "AWAY" if adj > 0 else "HOME"
    return 1 if model == f5_result else 0


def _f5_correct(f5_rec, model_dir, f5_result) -> str | int:
    try:
        if int(f5_rec or 0) != 1:
            return ""
    except (TypeError, ValueError):
        return ""
    if not f5_result or f5_result == "Tie":
        return ""
    return 1 if model_dir == f5_result else 0


# ── New columns that update_scores now populates ──────────────────────────────
NEW_COLS = (
    "away_f5", "home_f5", "f5_total", "f5_result", "f5_lean", "f5_correct",
    "away_innings", "home_innings",
    "away_sp_exit_inn", "away_sp_exit_score",
    "home_sp_exit_inn", "home_sp_exit_score",
)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="Update game_log with scores + F5 + starter-exit data")
    p.add_argument("--date", default="",
                   help="Date YYYY-MM-DD (default: yesterday)")
    args = p.parse_args()

    target = (args.date.strip() or
              os.environ.get("GAME_DATE", "") or
              (date.today() - timedelta(days=1)).isoformat())

    print(f"Fetching scores for {target}...")

    try:
        pks = get_gamepks(target)
    except Exception as e:
        print(f"  [!] Schedule fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not pks:
        print(f"  No games found for {target}")
        sys.exit(0)

    print(f"  Found {len(pks)} games — fetching live feeds...")

    results: dict[tuple, dict] = {}
    for pk in pks:
        r = get_game_result(pk)
        if r:
            key = (r["away"], r["home"])
            results[key] = r
            print(f"    {r['away']}@{r['home']:5}  {r['away_score']}-{r['home_score']}"
                  f"   F5: {r['away_f5']}-{r['home_f5']} ({r['f5_result']})"
                  f"   SP exit: away inn {r['away_sp_exit_inn']} @ {r['away_sp_exit_score']}"
                  f" | home inn {r['home_sp_exit_inn']} @ {r['home_sp_exit_score']}")

    if not results:
        print(f"  No final games found for {target}")
        sys.exit(0)

    if not LOG_FILE.exists():
        print(f"  [!] {LOG_FILE} not found", file=sys.stderr)
        sys.exit(1)

    with open(LOG_FILE, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        sys.exit(0)

    fields = list(rows[0].keys())
    # Ensure all new columns exist in the field list
    for col in NEW_COLS:
        if col not in fields:
            # Insert after home_score / f5_correct depending on the column
            anchor = "f5_correct" if col in (
                "away_innings","home_innings",
                "away_sp_exit_inn","away_sp_exit_score",
                "home_sp_exit_inn","home_sp_exit_score") else "home_score"
            idx = fields.index(anchor) + 1 if anchor in fields else len(fields)
            fields.insert(idx, col)

    updated = 0
    for row in rows:
        if row.get("game_date", "") != target:
            continue
        key = (row.get("away_team", ""), row.get("home_team", ""))
        if key not in results:
            continue

        res  = results[key]
        a_sc = res["away_score"]
        h_sc = res["home_score"]

        row["away_score"]         = a_sc
        row["home_score"]         = h_sc
        row["away_f5"]            = res["away_f5"]
        row["home_f5"]            = res["home_f5"]
        row["f5_total"]           = res["f5_total"]
        row["f5_result"]          = res["f5_result"]
        row["away_innings"]       = res["away_innings"]
        row["home_innings"]       = res["home_innings"]
        row["away_sp_exit_inn"]   = res["away_sp_exit_inn"]   or ""
        row["away_sp_exit_score"] = res["away_sp_exit_score"] or ""
        row["home_sp_exit_inn"]   = res["home_sp_exit_inn"]   or ""
        row["home_sp_exit_score"] = res["home_sp_exit_score"] or ""
        row["lean"]               = _lean(row.get("composite"), a_sc, h_sc)
        row["model"]              = _model(row.get("composite"), row.get("model_dir"),
                                           row.get("qualified"), a_sc, h_sc)
        row["f5_lean"]            = _f5_lean(row.get("composite"), res["f5_result"])
        row["f5_correct"]         = _f5_correct(row.get("f5_rec"), row.get("model_dir"),
                                                 res["f5_result"])
        updated += 1

    print(f"\n  Updated {updated} rows")

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"  [✓] {LOG_FILE} saved")


if __name__ == "__main__":
    main()
