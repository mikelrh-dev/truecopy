# Exploration: Post-audit hardening

Change: `post-audit-hardening`. Date: 2026-10-10. Follows the answer-cache and
per-manual-calibration cycles and their merge to `main`.

## Current State (verified against code, pre-change)

- **Identity.** `scripts/ingest.py` and `scripts/reindex.py` compute
  `manual_id` from the PDF stem (or `--manual-id`). `web/app.py` stages the
  upload under a UUID name and hardcoded `manual_id="manual-actual"` in
  `_do_upload` (and a now-vestigial assignment in the no-op `_do_reindex`).
- **Golden/key consequence.** `scripts/eval.py` `GOLDEN_SETS` are keyed by the
  PDF-stem `manual_id`; calibration records are keyed by the ingested id
  verbatim. A web-ingested manual therefore missed its golden set and wrote to a
  single shared key. The per-manual-calibration explore flagged this as an
  explicit decision ("CLI PDF-stem and web `manual-actual` are separate keys")
  and deferred normalization; this change resolves it in the correct direction.
- **Log provenance.** `monitoring.QueryRecord` has a `revision` field, but
  `build_record` hardcoded `""`, so the field was always empty. The cache path in
  `answer_question` already reads `current_revision` once, so the revision is
  available for free on both the hit and the miss paths.
- **Advisory coverage.** `scripts/ingest.py` prints `calibration.advisory(...)`
  after a successful swap; `scripts/reindex.py` (the replacement path) does not.
- **Eval record.** `scripts/eval.py` appends a calibration record only when every
  check passes AND the sweep produced a point. It never told the owner when it
  did not append.

## Constraints

- The eval stdout is a frozen contract (pinned byte-for-byte by tests): any new
  diagnostic must go to stderr.
- The web upload filename must not reach the filesystem (traversal safety): the
  identity is derived from the name, the staged path stays a UUID.
- Local-only: sanitizing the filename is stdlib (`re`), no new dependency.

## Affected surface

`src/plant_rag/ingest/reindex.py` (new helper), `scripts/ingest.py`,
`scripts/reindex.py`, `web/app.py`, `src/plant_rag/monitoring.py`,
`src/plant_rag/answer/pipeline.py`, `scripts/eval.py`, and the tests for each;
delta specs on `manual-ingestion`, `min-score-calibration`, `answering`.
