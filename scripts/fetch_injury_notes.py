#!/usr/bin/env python3
"""
fetch_injury_notes.py
======================
Pulls recent MLB roster transactions (IL placements, activations, callups)
for teams playing today, and writes a side-channel reference table.

This is intentionally NOT wired into the composite math. team_batting.csv
and pitcher projections are season aggregates with no way to know who's
actually active tonight — this file exists purely so a human (or Claude,
mid-analysis) can cross-check "is anyone notable out for this game" without
having to manually web-search every team every day.

Source: MLB Stats API /api/v1/transactions — official, free, no key needed.
Covers IL placements/activations, callups/options, and other roster moves.
Looks back 5 days by default to catch moves that may still be relevant
(a 10-day IL placement from 4 days ago is still "out tonight").

Output: data/injury_notes.csv
  game_date, team, player, transaction_type, txn_date, description, relevant_today
"""

import csv
import json
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

OUT_DIR     = Path("data")
OUT_FILE    = OUT_DIR / "injury_notes.csv"
PP_FILE     = OUT_DIR / "probable_pitchers.csv"
LOOKBACK_DAYS = 5
TIMEOUT     = 25

TXN_URL = "https://statsapi.mlb.com/api/v1/transactions"

# Transaction types worth surfacing. MLB's typeDesc strings vary; match by
# substring on a lowercased description/typeDesc rather than an exact enum,
# since the API isn't perfectly consistent about casing/wording.
RELEVANT_KEYWORDS = (
    "injured list", "il ", "10-day", "15-day", "60-day",
    "activated", "reinstated", "optioned", "recalled",
    "designated for assignment", " placed ", "rehab assignment",
)

TEAM_ID_TO_ABBR = {
    109:"ARI",144:"ATL",110:"BAL",111:"BOS",112:"CHC",145:"CWS",
    113:"CIN",114:"CLE",115:"COL",116:"DET",117:"HOU",118:"KCR",
    108:"LAA",119:"LAD",146:"MIA",158:"MIL",142:"MIN",121:"NYM",
    147:"NYY",133:"ATH",143:"PHI",134:"PIT",135:"SDP",137:"SFG",
    136:"SEA",138:"STL",139:"TBR",140:"TEX",141:"TOR",120:"WSH",
}
NORM = {"AZ":"ARI","KC":"KCR","SD":"SDP","SF":"SFG","TB":"TBR","OAK":"ATH","LAN":"LAD"}


def _fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-injury-notes/1.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def _todays_teams():
    """Return the set of team abbreviations playing today, from probable_pitchers.csv."""
    if not PP_FILE.exists():
        print(f"  [!] {PP_FILE} not found — cannot scope to today's teams", file=sys.stderr)
        return set()
    teams = set()
    with open(PP_FILE, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            at = NORM.get(row.get("away_team",""), row.get("away_team",""))
            ht = NORM.get(row.get("home_team",""), row.get("home_team",""))
            if at: teams.add(at)
            if ht: teams.add(ht)
    return teams


def _is_relevant(txn: dict) -> bool:
    blob = f"{txn.get('typeDesc','')} {txn.get('description','')}".lower()
    return any(k in blob for k in RELEVANT_KEYWORDS)


def main():
    print("Fetching recent roster transactions...")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    today_teams = _todays_teams()
    if today_teams:
        print(f"  Scoping to {len(today_teams)} teams playing today: {sorted(today_teams)}")
    else:
        print("  [~] No today's-teams scope found — will include all teams")

    # Anchor to US/Central, not the runner's UTC clock — see log_games.py
    # for the full explanation of this bug pattern.
    end = datetime.now(ZoneInfo("America/Chicago")).date()
    start = end - timedelta(days=LOOKBACK_DAYS)
    url = f"{TXN_URL}?startDate={start.isoformat()}&endDate={end.isoformat()}&sportId=1"

    try:
        data = _fetch(url)
    except Exception as e:
        print(f"  [✗] Transactions fetch failed: {e}", file=sys.stderr)
        # Don't fail the whole pipeline over this — write an empty file with
        # headers so downstream steps don't choke on a missing file.
        with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["game_date","team","player","transaction_type","txn_date",
                 "description","relevant_today"])
        print(f"  [~] Wrote empty {OUT_FILE} so downstream steps don't break")
        sys.exit(0)

    txns = data.get("transactions", [])
    print(f"  Fetched {len(txns)} total transactions (last {LOOKBACK_DAYS} days)")

    rows = []
    for t in txns:
        # MLB API uses toTeam/fromTeam (not a flat 'team' field) for most
        # transaction types. toTeam is the team gaining the player (the
        # relevant one for "who's on this team's IL right now" purposes);
        # fall back to fromTeam, then a flat 'team' field if either appears
        # in some response variant.
        team_obj = t.get("toTeam") or t.get("fromTeam") or t.get("team") or {}
        team_id = team_obj.get("id")
        abbr = TEAM_ID_TO_ABBR.get(team_id)
        if not abbr:
            continue
        if today_teams and abbr not in today_teams:
            continue
        if not _is_relevant(t):
            continue

        rows.append({
            "game_date":         end.isoformat(),
            "team":              abbr,
            "player":            t.get("person", {}).get("fullName", ""),
            "transaction_type":  t.get("typeDesc", ""),
            "txn_date":          t.get("date", ""),
            "description":       (t.get("description", "") or "")[:200],
            "relevant_today":    1,   # all rows here already passed the keyword filter
        })

    # Most recent first
    rows.sort(key=lambda r: r["txn_date"], reverse=True)

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["game_date","team","player","transaction_type","txn_date",
                      "description","relevant_today"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  [✓] {OUT_FILE} — {len(rows)} relevant roster moves for today's teams")
    for r in rows[:15]:
        print(f"    {r['team']:<5} {r['player']:<24} {r['transaction_type'][:30]:<30} {r['txn_date']}")
    if len(rows) > 15:
        print(f"    ... and {len(rows)-15} more")


if __name__ == "__main__":
    main()
