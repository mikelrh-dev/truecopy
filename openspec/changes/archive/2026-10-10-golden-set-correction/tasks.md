# Tasks: Golden-set correction (indicator-lights case)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~15 (one expectation + its comment) |
| 400-line budget risk | None |
| Chained PRs recommended | No |
| Suggested split | Single commit |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: None

## Phase 1: Measure the corpus, then correct

- [x] 1.1 Read the real section titles from the live corpus for pages 15, 16, 75, 76, 79, 81 (`[Indicator Lights]` on 15/16; 75 High Beam Headlights, 79 Emergency Braking, 81 Parking Brake)
- [x] 1.2 Run ideal English queries (`"warning lights"`, `"warning lights indicator telltale dashboard"`) against the serving path: neither surfaces 75/81 → not a cross-lingual recall gap
- [x] 1.3 RED: with `{15, 16}` the check is satisfiable only by the real section (the old set was satisfied by an adjacent headlight page)
- [x] 1.4 GREEN: expectation changed to `frozenset({15, 16})` with the evidence comment

## Phase 2: Verification

- [x] 2.1 `.venv/Scripts/python.exe -m pytest tests -q` → 363 passed, exit 0 (no test keys on the real golden values, so the suite is unaffected)
- [x] 2.2 Live eval `.venv/Scripts/python.exe scripts/eval.py --no-calibration` → **ALL CHECKS PASSED (4/4)**, golden grounding reports the case landing on page 15 with `expected one of [15, 16]`
- [x] 2.3 Carried into the block plan: the abandoned glossary change is recorded as cancelled-by-evidence
