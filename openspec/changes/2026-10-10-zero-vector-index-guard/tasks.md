# Tasks: Zero-vector index guard

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~60 (code ~20, tests ~40) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, one work unit (guard + test) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: Low

Status: **guard implemented** (`ingest/reindex.py`), spec delta applied, carried
items (C1-C4) still open. The operational repair of the incident is recorded in
`proposal.md`.

Open question resolved: the guard lives in `ingest/reindex.py` only — the
embedder also serves queries, and a query vector of zeros is merely a bad query,
not corrupt index data.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | A zero-vector encoder cannot produce an aliased revision | `.venv/Scripts/python.exe -m pytest tests/test_ingest.py -q` | Revert the guard hunk + its test |

## Phase 1: Guard the write path

- [x] 1.1 RED: `tests/test_ingest.py` — an encoder returning all-zero vectors raises `ValueError` naming the `child_id`, and no `current_*` alias moves (the previous revision still serves). Confirmed RED first: `Failed: DID NOT RAISE ValueError`
- [x] 1.2 GREEN: in `ingest/reindex.py`, after the dimensionality check, reject a vector whose every component is ~0 (`abs(value) < 1e-12`) before upserting the batch
- [x] 1.3 Confirm the incomplete revision is discarded (`_discard`) exactly like a dimensionality failure, so the failure is not half-aliased

## Phase 2: Spec delta

- [x] 2.1 `openspec/specs/manual-ingestion/spec.md` gains "Encoder output is real evidence" (2 scenarios: zero vector fails the ingest; a non-zero vector of the right shape proceeds)

## Phase 3: Verification

- [x] 3.1 `.venv/Scripts/python.exe -m pytest tests -q` → **363 passed**, exit 0 (was 362); `tests/test_ingest.py` alone 36 passed
- [x] 3.2 `ruff check src/plant_rag/ingest/reindex.py tests/test_ingest.py` → clean
- [x] 3.3 Live fail-loud check against the real stack: generated PDF, zero encoder → `encode returned an all-zero vector for guard_probe:r10:p1:c0: refusing to index a chunk with no signal`; alias stayed `manual_r9_text` and revisions stayed `{8, 9}` (incomplete r10 discarded)

## Carried (decide separately, not blockers)

- [ ] C1 Decide whether `manual_r8_*` stays as rollback evidence or is deleted (currently retained on purpose)
- [ ] C2 Run the live eval against r9 and close roadmap item 2 / W3 (the four goldens now land in top-k; that is an inference until measured)
- [ ] C3 Optional: promote the repair procedure from the gitignored
      `outputs/rag-probe/rebuild_r9.py` to a maintenance entrypoint under
      `scripts/`, with the zero-vector check it already carries
- [ ] C4 Record the r9 revision change wherever the operator history is read,
      since calibration records for r8 are now hard-stale by revision
