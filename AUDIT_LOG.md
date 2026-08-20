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

**Standing policy (established 2026-08-10, TIGHTENED to an allowlist same
day): a rule combo only qualifies as a live bettable play once it has
independently proven itself with n>=10 DECIDED games (pushes/ties
excluded) AND a win rate >=55%.** This is stricter than a simple block
threshold -- it's an allowlist, not a block-list: a combo doesn't qualify
by default and then get excluded for being bad, it must actively earn
qualification by clearing both the sample-size and win-rate bars. This
means brand-new combos and currently-good-looking small samples (e.g. V3's
R1+R2+R3+R4 at 83.3% but only n=6 decided games, or V4's F4 alone at 71.4%
but only n=7) are blocked purely on insufficient sample size until they
accumulate more decided games -- not because they look bad, but because
they haven't yet proven themselves. Implemented as static (win, loss)
record dicts keyed by exact frozenset combo, refreshed at each full audit
rather than computed dynamically on every pipeline run -- consistent with
this pipeline's general preference for periodic deliberate audits over
continuous self-modification. Every combo still gets logged (rules_str
populated) regardless of allowlist status, so tracking never stops. At
every audit: recompute every combo's (win, loss) record from scratch,
update the allowlist dict, and note which combos newly qualified or fell
out.

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

## 2026-08-10 (later same day) — systematic threshold policy adopted

- Formalized the ad-hoc blocking approach (F1+F2+F3, blocked earlier today)
  into a standing policy: any combo below 50% win rate on n>=10 games gets
  blocked, full stop, rather than deciding case by case whether a given
  finding is "clear enough" to act on.
- A comprehensive re-run of every V3/V4 combo (n=836 games, June 1 - Aug 10)
  surfaced two more combos meeting this threshold that hadn't been
  individually flagged before:
  - **V3 R2+R4**: 30.0% (3W-7L, n=10) -- worsened from 42.9% (n=7) at the
    8/6 audit. Now blocked.
  - **V3 R1+R3+R2**: 42.9% (3W-4L-4P, n=11) -- was a "watch" item since
    8/6 (33.3%, n=10), now clears the n>=10 bar for action. Blocked.
  - **V4 F1+F3+F4**: 42.9% (6W-8L, n=14) -- a new finding, not previously
    flagged. Distinct from the healthy F1+F2+F3+F4 superset (64.7%, n=17).
    Blocked.
- **Fixed a real gap in the original F1+F2+F3 block**: it left the
  rules_str field empty when blocked, meaning that combo would have gone
  dark with zero new tracked data points going forward -- silently
  defeating the "keep tracking it" half of the policy before the policy
  was even formalized. Both V3 and V4 blocking now always populate the
  rules_str field when a combo would otherwise have qualified, blocked or
  not.
- Current full block list: V3 {R1,R2,R3}, V3 {R2,R4}; V4 {F1,F2,F3}, V4
  {F1,F3,F4}.
- All changes verified in isolation (10 scenarios covering both blocked
  and unblocked cases, both models) before live testing and deployment.

## 2026-08-10 (third entry same day) — policy tightened to an allowlist

- User requested the block-list approach be tightened further: instead of
  "block anything clearly bad," only ALLOW combos that have proven n>=10
  decided games AND >=55% win rate. Everything else is blocked by default,
  including combos that have never been individually flagged as bad --
  they simply haven't earned qualification yet.
- This is a substantially bigger behavioral change than the two earlier
  entries today. Recomputed every V3/V4 combo's exact (win, loss) record
  on decided games only (pushes/ties excluded) and built the initial
  allowlist:
  - **V3 allowed**: R1+R4 (57.9%, n=19), R3 (55.6%, n=18), R3+R4 (69.2%,
    n=13), R1+R2 (63.6%, n=11), R1+R3 (63.6%, n=11), R1+R3+R4 (60.0%,
    n=10), R5 counter (68.4%, n=19).
  - **V3 now blocked purely on sample size** (would otherwise look fine
    or great): R1+R2+R3+R4 (83.3%, n=6), R2+R3 (66.7%, n=9).
  - **V3 blocked on both sample size and rate**: R2+R4 (30.0%, n=10),
    R1+R2+R3 (42.9%, n=7), R1+R2+R4 (50.0%, n=6), R2+R3+R4 (0.0%, n=2).
  - **V4 allowed**: F2 alone (55.6%, n=18), F1+F2+F3+F4 (64.7%, n=17),
    F5-rule (67.1%, n=70).
  - **V4 now blocked purely on sample size**: F4 alone (71.4%, n=7),
    F2+F3 (100%, n=2), F3+F4 (66.7%, n=3), F2+F4 (66.7%, n=3), F1+F2+F4
    (100%, n=1) -- several of these look excellent but have almost no
    track record yet.
  - **V4 blocked on rate**: F1+F2+F3 (31.2%, n=16), F1+F3+F4 (42.9%,
    n=14), F2+F3+F4 (50.0%, n=4), F1+F2 (25.0%, n=4), F1+F3 (0%, n=2),
    F1+F4 (0%, n=1).
- Net effect: today's qualifying-play count dropped sharply (verified live
  -- only 1 of 10 games on the current slate produced a bettable play,
  versus multiple under the previous block-list policy). This is expected
  and intended given the stricter bar, not a bug.
- All changes verified in isolation (11 scenarios across V3/V4/R5/F5-rule)
  before live testing and deployment.

## 2026-08-20 — fourth full audit (n=969 games, June 1 - Aug 19)

Scheduled audit, run on time per the standing cadence.

- **V3 `R3` alone dropped out of the allowlist.** Was 55.6% (n=18) at the
  8/10 audit; now 47.6% (10W-11L, n=21) -- a real decline on a growing
  sample, not noise. This was one of the most frequently-firing V3
  signals all season, so this is a meaningful narrowing of future plays.
- **V4 `F2` alone dropped out of the allowlist.** Was 55.6% (n=18) at the
  8/10 audit; now exactly 50.0% (10W-10L, n=20) -- same pattern as R3
  above.
- Both combos are kept in their record dicts (not deleted) specifically
  so tracking continues uninterrupted -- if either climbs back above 55%
  on n>=10 at a future audit, it's a natural candidate for re-inclusion,
  the allowlist mechanism doesn't need any code change to make that
  happen, just updated numbers.
- Everything else that was already allowed stayed allowed, mostly with
  improved or stable rates: R1+R4 59.3% (n=27), R3+R4 73.3% (n=15),
  R1+R3 64.3% (n=14), R1+R2 61.5% (n=13), R1+R3+R4 63.6% (n=11), R5
  counter 71.4% (n=21), F1+F2+F3+F4 57.9% (n=19), F5-rule 65.3% (n=75).
- **Watch list for next audit:**
  - V3 R2+R3: 54.5% (6W-5L, n=11) -- a near-miss, just under the bar.
  - V3 R1+R2+R3+R4: 83.3% (5W-1L, n=6) -- excellent rate, still under
    the n>=10 sample bar.
  - V4 F1+F3+F4: 42.9% (n=14), F1+F2+F3: 26.3% (n=19) -- both remain
    clearly blocked, consistent with prior findings.
- All changes verified in isolation before live testing; tested against
  today's 15-game slate with no errors.

**Operational note, unrelated to rule findings:** `game_log.csv` has
grown past GitHub's 1MB inline-content limit for the Contents API (now
~1,459 rows). Reading it now requires fetching via the raw
`raw.githubusercontent.com` download URL rather than the Contents API's
base64-encoded response, which silently returns empty content for files
over that size. Worth remembering for all future reads of this file.

---

## Next audit due

**~2026-09-03** (or ~150 new qualifying games from 8/20, whichever comes
first). Carry over from this audit: (1) re-check R2+R3 (54.5%, n=11) and
R1+R2+R3+R4 (83.3%, n=6) against the n>=10/55% bar; (2) re-check whether
R3 alone or F2 alone have recovered above 55%; (3) the row-loss issue
found on 8/10 (some historical doubleheader dates show only one row per
matchup instead of two) is still open and not yet root-caused.
