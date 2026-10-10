---
title: Archive Report — per-manual-calibration
type: architecture
created: 2026-10-10
updated: 2026-10-10
change: per-manual-calibration
verdict: pass_with_warnings
---

# Archive Report: per-manual-calibration

**Change**: per-manual-calibration
**Archived to**: `openspec/changes/archive/2026-10-10-per-manual-calibration/`
**Archived on**: 2026-10-10
**Artifact store**: hybrid — this file + Engram topic `sdd/per-manual-calibration/archive-report`
**Branch at close**: `feat/per-manual-calibration` (8 commits on `origin/main`)

## Verdict at close

**PASS WITH WARNINGS** — implementation complete, verified, warnings closed or documented.
No CRITICAL findings; nothing blocks archive.

## Final state (per Final-State Authority hierarchy)

The verify-report is an intermediate snapshot. Later work changed two of its claims;
this report records the state AT CLOSE:

| Snapshot claim (verify-report, Engram 2822) | Final state at close |
|---|---|
| W1 — ingest CLI stale path proven only on the age (soft-stale) branch; revision mismatch covered only at unit level | **CLOSED** — `tests/test_ingest.py` `test_ingest_hard_stale_when_the_stored_revision_differs` seeds revision `r2` against a landed `r3` and asserts the `[hard-stale]` advisory. Commit `932cc9d`. |
| W2 — real `golden_pass`/`golden_total` mapping in `check_golden_grounding` proven only by static reading | **CLOSED** — `tests/test_scripts.py` `test_check_golden_grounding_maps_real_pass_and_total_counts` drives the actual function on both shapes (passed → 2/2; failed → `None`/`None`). Commit `932cc9d`. |
| Suite 326/326 | **328/328** at close (the two W1/W2 tests added), verified by the orchestrator inline on the branch. |

## Still open at close (carried, not silently dropped)

- **S1** (suggestion from verify): spec R9 says `--record` "SHALL append exactly one
  calibration record", but the append gate at `scripts/eval.py:1005` also requires
  `report.points` (design decision 8), so a fully passing eval with an empty sweep
  appends nothing. Not a defect (deterministic, documented in design), but the delta
  wording could be narrowed. No source change.
- **Observation (not fixed, out of this change's scope)**: `openspec/specs/answering/spec.md`
  assumption A4 still reads "a scripted eval set SHALL land once a test runner exists",
  a stale greenfield clause from before the answer-cache cycle replaced the requirement
  below it. Left untouched here; a future change should clean it.
- **Deferred by decision**: `scripts/reindex.py` advisory wiring (v1 wires only
  `scripts/ingest.py`); hybrid-path sweep and `TEXT_MIN_SCORE`; web surface;
  `manual_id` normalization; feedback coupling.

## Gates

| Gate | Result | Evidence |
|---|---|---|
| Task completion | PASS | 12/12 tasks `[x]` in `tasks.md`, 0 unchecked |
| CRITICAL issues | PASS | 0 in verify-report; none introduced after |
| Native review receipt | PASS (absent) | `reviewGate` structurally absent — no review ever started for this candidate → archive proceeds under ordinary repository policy |
| Change status | PASS | `gentle-ai sdd-status per-manual-calibration` → `archive: ready`, `blockedReasons: []` |
| Mechanical copy | PASS | `diff -r` snapshot vs archive destination: empty, exit 0 |

## Specs synced

| Domain | Action | Details |
|---|---|---|
| `min-score-calibration` | Created | new full spec, 8 requirements / 12 scenarios — mechanically copied (byte-identical, `diff -r` empty) |
| `answering` | Updated | +1 requirement appended: "Opt-in calibration recording in eval" (4 scenarios); the 5 pre-existing requirements preserved |
| `manual-ingestion` | Updated | +1 requirement appended: "Advisory calibration notice after reindex" (4 scenarios); the 3 pre-existing requirements preserved |

## Archive contents

- `proposal.md` ✅ · `explore.md` ✅ · `design.md` ✅ · `tasks.md` ✅ (12/12) · `verify-report.md` ✅ · `specs/` (3 delta specs verbatim)
- `archive-report.md` (this file, additive — excluded from the copy readback)

## Source of truth updated

- `openspec/specs/min-score-calibration/spec.md` (new)
- `openspec/specs/answering/spec.md` (6 requirements)
- `openspec/specs/manual-ingestion/spec.md` (4 requirements)

## Engram traceability

Observations read this cycle: `sdd/per-manual-calibration/explore` (2813),
`proposal` (2816), `spec` (2817), `design` (2818), `tasks` (2819),
`apply-progress` (2820, 2823), `verify-report` (2822).
Dispatched by the orchestrator inline (sub-agent quota exhausted at the time);
the W1/W2 closure batch and the archive phases were executed by the orchestrator
directly, following the same skill contracts.

## SDD Cycle Complete

Planned, implemented, verified, warnings closed, and archived.
Delivery pending (orchestrator): push `feat/per-manual-calibration` and open the
single PR against `main`.
