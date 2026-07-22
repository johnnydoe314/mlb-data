#!/usr/bin/env python3
"""
umpire_diagnostic.py
=====================
One-off diagnostic: fetch a handful of real boxscores from MLB's official
Stats API and inspect the exact structure of the "officials" field, so we
can build a real umpire-assignment/tendency pipeline based on confirmed
field names rather than guesswork.

Not part of the daily pipeline. Writes findings to data/umpire_diagnostic.txt
for inspection.
"""

import json, urllib.request, urllib.error
from pathlib import Path

OUT_DIR = Path("data")
OUT_FILE = OUT_DIR / "umpire_diagnostic.txt"

# A few real, varied gamePks to check structure across different games/eras
TEST_GAME_PKS = [745444, 716463, 717465]

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch_boxscore(game_pk):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    lines = []
    for pk in TEST_GAME_PKS:
        lines.append(f"\n{'='*60}\ngamePk={pk}\n{'='*60}")
        try:
            data = fetch_boxscore(pk)
        except Exception as e:
            lines.append(f"FETCH FAILED: {e}")
            continue

        top_keys = list(data.keys())
        lines.append(f"Top-level keys: {top_keys}")

        officials = data.get("officials")
        if officials is None:
            lines.append("No 'officials' key found in response.")
        else:
            lines.append(f"officials type: {type(officials).__name__}")
            lines.append(f"officials raw: {json.dumps(officials, indent=2)}")

        # Also check 'info' for any umpire-related free text
        info = data.get("info", [])
        for section in info:
            if "ump" in json.dumps(section).lower():
                lines.append(f"Umpire-related info section: {json.dumps(section, indent=2)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w") as fp:
        fp.write("\n".join(lines))
    print(f"Wrote diagnostic output to {OUT_FILE}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
