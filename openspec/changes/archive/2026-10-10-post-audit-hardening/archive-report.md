---
title: Archive Report — post-audit-hardening
type: architecture
created: 2026-10-10
updated: 2026-10-10
change: post-audit-hardening
verdict: pass
---

# Archive Report: post-audit-hardening

**Change**: post-audit-hardening (audit findings A1/A2 + advisory coverage + eval note)
**Archived to**: `openspec/changes/archive/2026-10-10-post-audit-hardening/`
**Archived on**: 2026-10-10
**Artifact store**: hybrid — this file + Engram topic `sdd/post-audit-hardening/archive-report`
**Branch at close**: `feat/post-audit-hardening` (4 code commits on `6de5ce4`)

## Verdict at close

**PASS** — no CRITICAL findings, no blockers, no warnings.

## Final state

| Finding | Final state at close |
|---|---|
| A1 — web used a shared `manual-actual` id | `manual_id_from_filename()` is the single identity for the ingest CLI, the reindex CLI, and the web upload; the vestigial `manual-actual` assignment in `_do_reindex` was removed |
| A2 — the log never recorded the corpus revision | `build_record(..., revision=)` is populated by the answering chain on both the hit and miss paths (`r{N}`); empty only when no corpus is resolved; harness traffic reads none |
| Reindex CLI missing the advisory | `scripts/reindex.py` prints the same read-only advisory as `scripts/ingest.py` |
| `--record` could fail silently | A stderr reason (red checks / empty sweep / disabled path) with byte-identical stdout |

## Gates

| Gate | Result |
|---|---|
| Task completion | PASS — 0 unchecked in `tasks.md` |
| CRITICAL issues | PASS — 0 |
| Tests | PASS — `355 passed`, exit 0 (was 346 on the base; +9 = 7 planned + 2 identity-helper tests) |
| Build | PASS — `compileall`, exit 0, empty output |
| No serving-behavior change | PASS — `git diff 6de5ce4..HEAD -- src/plant_rag/retrieve` empty |

## Specs synced

| Domain | Action | Details |
|---|---|---|
| `manual-ingestion` | Updated | +1 requirement "One identity namespace for ingested manuals" (2 scenarios); "Advisory calibration notice after reindex" extended to BOTH CLI entrypoints, the web-deferred clause removed (4 → 5 scenarios) |
| `min-score-calibration` | Updated | "Verbatim identifier keys" now states the single sanitized-filename namespace (the `manual-actual` split removed) + a share-the-key scenario |
| `answering` | Updated | +1 requirement "Logged answer provenance" (3 scenarios); "Opt-in calibration recording in eval" gained the stderr-reason clause and a scenario |

## Still open at close (carried)

- `multi-manual` must add `manual_id` to the answer-cache key; this change keeps
  it (revision, model, question).
- Historical `manual-actual` records are left untouched by design (a miss stays a
  clean "no record").
- `ROADMAP.md` items 1/2/3 (phase-2 retry, live eval, multi-manual) remain the
  pending horizon.

## Archive contents

- `proposal.md` · `explore.md` · `design.md` · `tasks.md` · `verify-report.md` · `specs/{manual-ingestion,min-score-calibration,answering}/spec.md` · `archive-report.md` (this file)

## SDD Cycle Complete

Planned, implemented, verified, and archived. The spec tree is again the source
of truth.
