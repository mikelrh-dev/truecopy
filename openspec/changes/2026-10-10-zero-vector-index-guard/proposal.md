# Proposal: Zero-vector index guard (silent dead corpus)

## Intent

The live corpus served by `current_text` had **every vector set to zero**. Dense
retrieval was therefore dead: cosine similarity is 0.0 against every point, the
calibrated floor (0.40) rejects everything, and with the pool-level gate of
`2026-10-10-post-audit-hardening-2` in place the candidate pool is empty for
every question — an operator asking something the manual answers gets the exact
refusal `No aparece en la documentación` with no LLM call.

Nothing in the pipeline reported it. That is the defect: the ingest path
validates the SHAPE of each vector (exactly `config.EMBED_DIMS` values) but never
its MAGNITUDE, so an encoder that returns zeros produces a fully dead
revision that validates, aliases, and serves refusals forever.

This change adds the guard. It does NOT undo the incident: the operational
repair is recorded below as already performed.

## Evidence (measured on the live stack, 2026-10-10)

| Check | Result |
|---|---|
| `manual_r8_text` points / config | 1211 points; `size=1024`, `distance=Cosine`, `on_disk=True`, status green |
| Sampled vectors | **300/300 all-zero** (`nz_dims=0`, `norm=0.0000`); same via raw REST `with_vector=true` (length 1024, norm 0.0) |
| Self-query by point id (vector read server-side from disk) | top-5 all `score=0.0`, and the queried id is not in its own top-3 → the zeros are on disk, not a client artifact |
| Embedder (live) | `encode` → 1024 dims, `nz=1024`, `norm=1.0000` → the model is healthy |
| Alias / revision contract | `current_text → manual_r8_text`, `revision_tag → r8` → correct |
| Text branch | alive: `MatchText "tire pressure"` → pages 207, 294, 310, 205, 210; `"autosteer"` appears in 81 `child_text` values |
| Consequence before the gate | dense returned nothing; text-only token matches were the only possible evidence |
| Consequence after the gate | pool empty → exact refusal, no LLM call, latency 0.4 s |

Root cause (hypothesis, stated as such): the r8 revision was built by an encoder
returning zeros — a placeholder/stub ingest of the kind suggested by the
`qdrant_data/_phase2_harness/` artifacts on this machine — and the pipeline
accepted it because only dimensionality is validated. No code path in `src/`
manufactures zeros, so this is not a runtime fallback but an ingested artifact.

## Operational repair (already done, recorded for the trail)

- `outputs/rag-probe/rebuild_r9.py` (gitignored scratch): re-embedded the 1211
  stored `child_text` values with the real BGE-M3, wrote `manual_r9_text`
  (Cosine/1024/on_disk + `child_text` index) and `manual_r9_alarms`, kept
  payloads, provenance and pages verbatim, then `validate_revision(9, 1211, 0)`
  and `swap_aliases(9)` — 843.7 s at ~1.4 texts/s.
- Verified after the swap: 1211/1211 points, 0/100 sampled vectors zero,
  norm 1.0000, self-query 1.0, `current_text → manual_r9_text`,
  `current_alarms → manual_r9_alarms`.
- `manual_r8_*` was deliberately NOT deleted: it is unreachable through any
  alias and stays as rollback evidence (ADR-001 blue/green deletes it; this
  repair chose to keep it — a decision for the change below to confirm).
- Side effect to accept knowingly: calibration records keyed by revision r8 are
  now HARD-STALE (revision mismatch). They keep being surfaced with downgraded
  wording and counts, never hidden.

## Scope

### In Scope

- `src/plant_rag/ingest/reindex.py`: reject an all-zero vector BEFORE any upsert,
  failing loudly with the offending `child_id` (same fail-closed convention as
  the dimensionality check).
- Hermetic test in `tests/test_ingest.py`: a zero-returning encoder fails the
  ingest and leaves no aliased revision behind.
- Delta spec for `manual-ingestion`: encoder output must be real evidence.

### Out of Scope (proposed follow-ups, not this change)

- A maintenance entrypoint for "rebuild vectors from stored texts" (today the
  repair lives in a scratch script under a gitignored path).
- `validate_revision` sampling vectors for degeneracy: the guard at write time
  is the cheap, sufficient place.
- Deleting `manual_r8_*`.
- The live eval run that closes roadmap item 2 (W3).

## Risks

| Risk | Mitigation |
|---|---|
| A legitimate encoder could emit a zero vector for a degenerate input (e.g., an empty child text) | The pipeline already skips pages with no children (`if children`); the guard names the `child_id` so the failing input is actionable |
| The guard rejects a deliberate stub-ingest harness | Stub ingest remains possible by injecting an encoder that returns a tiny non-zero constant; the point is that a dead index must not be aliased silently |
| Re-fixing r8-shaped incidents by rebuilding hides the real bug | Both halves are recorded: the guard (this change) and the repair procedure (above) |

## Rollback

Revert the ingest guard and its test. No data migration: the guard only changes
what a future ingest accepts, and `manual_r9_*` is unaffected.
