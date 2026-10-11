# Proposal: Correct the mis-recorded indicator-lights golden

## Intent

The golden set is the eval's ground truth: a case declares the pages a question
is allowed to land on, and "a manual whose set declares expectations that were
never verified" is precisely what the check exists to prevent. One Owners_Manual
case was recorded from the wrong pages, so the check passed for the wrong
reason — and a retrieval "recall gap" was chased as a result.

## Evidence (measured on the live corpus, 2026-10-10)

The case `que luces de testigo hay en el cuadro` expected `{75, 76, 79, 81}`.
The corpus's own section titles say otherwise:

| Page | Section title in the corpus | Really about |
|---|---|---|
| 15 | `[Indicator Lights]` | the dashboard indicator lights (the answer) |
| 16 | `[Indicator Lights]` | the dashboard indicator lights (the answer) |
| 75 | `[High Beam Headlights]` | headlights |
| 76 | `[Headlight Indicator Lights]` | headlight indicator |
| 79 | `[Emergency Braking]` | braking |
| 81 | `[Parking Brake]` | brake |

Supporting measurements:

- The retrieval for the question returns pages 15 and 16 (the true section),
  plus 76/77 as adjacent headlight pages — the expectation set contained only
  the adjacent ones.
- An IDEAL English query (`"warning lights"`, `"warning lights indicator
  telltale dashboard"`) also fails to surface 75/81, which proves the missing
  pages were never a Spanish-to-English recall gap: the pages are simply not
  about indicator lights.
- The earlier abandoned glossary idea (adding "testigo" synonyms) is cancelled
  by that same measurement.

## Scope

### In Scope

- `scripts/eval.py`: the case's expectations become `{15, 16}`, with the
  evidence recorded in the comment (page-to-section mapping incl. what 75/79/81
  actually are).

### Out of Scope

- No change to retrieval, thresholds, chunking, or the evaluation harness.
- No relaxation of the check: `{15, 16}` is stricter than the four-page set it
  replaces, and it is the section the operator's question actually targets.
- No review of the other six cases here (they were sanity-checked against the
  corpus during the same measurement: their pages match their sections).

## Risks

| Risk | Mitigation |
|---|---|
| The correction makes the check pass more easily | It does the opposite: two pages of one section instead of four pages spread over three sections |
| Other cases are mis-recorded too | Declared as not reviewed case-by-case beyond the section check above; the live run prints every case's landed page for manual reading |
| Expectations keyed to one revision's pagination | Already true of every case; the goldens are re-verified against the live corpus on each reindex |

## Rollback

Revert one constant in `scripts/eval.py`. No data, config, or index involved.
