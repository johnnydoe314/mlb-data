# Full system audit log

Periodic full reimplementation of every V3/V4 rule from raw data, applied
uniformly across every qualifying game since June 1 (not just games we
actually bet), then graded against real outcomes. This is a different check
than daily grading — daily grading answers "did this bet win," a full audit
answers "is this rule structurally sound." Both R1's and F1's problems were
invisible day to day and only surfaced here.

**Cadence: every 2 weeks, or every ~150 new qualifying games since the last
audit, whichever comes first.** Check the date/count below before starting
any review or analysis session; if either threshold is passed, run a full
audit before or alongside that session.

---

## 2026-07-13 — first full audit

- **Removed R6** (no-park composite sweet spot). Live: 14.3% (2W-12L-3P)
  since its 7/1 introduction, vs 63.8% for non-R6 V3 plays over the same
  period. Every 4-5 rule "high confirmation" play since 7/4 involved R6 and
  went 0W-4L-2P. R6's 78.3% backtest (May-June, no-park-adjustment, n=43)
  did not generalize to live conditions.

## 2026-07-21 — second full audit

- **Demoted R1** from standalone-capable to confirm-only. R1 alone: 47.6%
  (10W-11L-2P, n=23) — below a coin flip. Adding R1 to already-strong
  combos consistently made them worse: R3 alone 75.0% -> R1+R3 50.0%;
  R3+R2 80.0% -> R1+R3+R2 33.3%. Everything without R1: 65.7%; everything
  with R1: 55.4%.
- Added `bp_alt` tracked-only signal (R2 variant, bp_cat=GOOD instead of
  NEUTRAL). Backtest: 72.7% (n=13).

## 2026-08-06 — third full audit (n=779 games, June 1 - Aug 6)

- **Demoted F1** from standalone-capable to confirm-only, same fix as R1.
  F1 present anywhere in the combo: 44.9% (22W-27L, n=49). F1 absent:
  66.7% (22W-11L, n=33). F2/F3/F4 don't show this problem; F4 presence
  actually correlates with *better* performance (58.7% vs 47.2%).
- **`roll_kbb` disconfirmed.** Original backtest: 55.4% (n=426, historical
  reconstruction). Live tracking: 46.9% (n=160) — below a coin flip.
  Correctly never promoted to a real rule. This is the discipline working
  as intended.
- V3 core overall: 59.3% (n=137), stable since the July 21 audit.
- R5 counter: 68.4% (n=23). V4 F5-rule: 69.2% (n=65). Both holding up well.
- **Watch list, not yet acted on** (samples still too thin to act):
  - V3 R1+R3+R2: 33.3% (n=10)
  - V3 R2+R4: 42.9% (n=7)
  - V3 R3+R2+R4: 0.0% (n=3, too small to mean anything yet)
  - V3 F3 (individual rule presence): 49.0% present vs 60.6% absent — a
    real but smaller, less conclusive gap than F1 had. Left alone.
- `bp_alt`: 33.3% (n=6) — still too thin to judge either way.
- `shadow_full`: 48.1% (n=27) — roughly coin-flip, as expected for a
  blended F5-outcome tracker (not itself a directional signal).

**Also found (separate from the rule audit, same session):** the
doubleheader row-conflation bug — five confirmed occurrences all season
(PIT@CLE 7/7, MIL@STL 7/7, PIT@CLE 7/18, LAD@NYY 7/19, ATL@NYM 7/29) — was
root-caused and fixed in `update_scores.py`. The `results` dict was keyed
only by (away_team, home_team), so a doubleheader's second game silently
overwrote the first, and both `game_log.csv` rows read the same
overwritten entry. Fixed by keying on (away, home, game_num), extracted
from `gameData.game.gameNumber` on the MLB Stats API feed. Validated
against real historical data (PIT@CLE 7/18) before deploying.

**Separate, still-open issue found while validating the above:** several
historical doubleheader dates now show only ONE row in `game_log.csv` per
matchup instead of two — a full row appears to have been lost somewhere
in the pipeline, not just a conflated score. This is a different bug from
the one just fixed (that one corrupts scores in place; this one drops a
row entirely). Not yet root-caused. Needs its own investigation before
the next audit.

## 2026-08-10 — out-of-cycle fix (not a full audit, targeted follow-up)

- **Blocked V4 combo F1+F2+F3** (without F4). This is a follow-on from the
  8/6 audit's F1 finding, but a distinct, more specific problem: F1's
  confirm-only demotion didn't fully address it, because F1+F2+F3
  continued to underperform badly even as a confirmed (2+ rule) combo --
  16.7% (2W-10L, n=12) as of 8/9, having lost 4 of its last 5 fires across
  8/6-8/9. Critically, F1+F2+F3+F4 (the 4-rule superset) is fine at 54.5%
  (n=11), and F2 alone is strong at 66.7% (n=9) -- so this isn't "F1 is
  bad broadly" (already handled), it's specifically this exact 3-rule
  combination missing F4 that's toxic. Implemented as a surgical exact-set
  exclusion (frozenset comparison), not a change to any individual rule's
  standalone status, so F1+F2+F3+F4 and every other combo pass through
  unaffected. Verified in isolation across 9 scenarios before deploying;
  tested live against the current slate with no errors.
- Decided to act out-of-cycle rather than wait for the 8/20 audit, given
  how clear and compounding the evidence had become (the combo fired twice
  in one day on 8/9, splitting 1W-1L, with the aggregate still badly
  underwater).

---

## Next audit due

**~2026-08-20** (or ~150 new qualifying games from 8/6, whichever comes
first). Carry over from this audit: (1) re-check the R1+R3+R2, R2+R4, and
F3 watch-list items now that they'll have ~2 more weeks of data; (2)
investigate and fix the row-loss issue found above; (3) confirm the F1 and
game_num fixes are behaving correctly in live data since deployment.
