# Proposal: Post-audit hardening (identity, log provenance, advistory coverage)

## Intent

A read-through of the shipped system after the answer-cache and
per-manual-calibration cycles surfaced four gaps between what the specs claim
and what the code does. Each is small; together they break the promise that the
artifacts are the source of truth. This change closes them and re-syncs the
specs. No new capability, no answer-behavior change beyond recording facts that
were already being computed and thrown away.

The four findings (internal review; labelled A1/A2 in the code comments):

- **A1 — two identity namespaces for the ingested manual.** `scripts/ingest.py`
  and `scripts/reindex.py` derive `manual_id` from the PDF stem, but the web
  upload hardcoded `"manual-actual"`. Golden sets (`GOLDEN_SETS` keyed by
  `manual_id`) and calibration records therefore NEVER matched a web-ingested
  manual, and every web upload overwrote the previous one's calibration record.
- **A2 — the query log never recorded the corpus revision.** `build_record`
  hardcoded `revision=""`, so after a reindex nothing in the operator history
  could be tied to the corpus that produced it — precisely the provenance the
  calibration advisory warns about.
- **Reindex CLI missing the advisory.** The per-manual-calibration change wired
  the read-only calibration advisory into `scripts/ingest.py` only; the
  replacement CLI (`scripts/reindex.py`) — the common operator path — was left
  as a deferred follow-up.
- **`eval.py --record` could fail silently.** An owner asking for a calibration
  record and getting none (red checks, empty sweep, disabled path) had no way to
  tell it apart from success.

## Scope

### In Scope

- `manual_id_from_filename()` in `ingest/reindex.py`: ONE sanitized identity for
  every entrypoint (CLI ingest, CLI reindex, web upload). The filesystem never
  carries the identity (uploads stage under a UUID); only the manual's name does.
- `web/app.py::_do_upload`: use `manual_id_from_filename(uploaded.name)` instead
  of the shared `"manual-actual"`.
- `scripts/reindex.py`: print the same read-only calibration advisory as
  `scripts/ingest.py` after a successful swap.
- `monitoring.build_record(..., revision=)`: stop hardcoding `""`; the answering
  chain resolves `current_revision` once when logging and stamps `r{N}` on both
  the cache-hit and the miss paths.
- `scripts/eval.py`: when `--record` produced no record, explain why on stderr
  (red checks / empty sweep / disabled path) without touching the frozen stdout.
- Delta specs: `manual-ingestion`, `min-score-calibration`, `answering`.
- Hermetic tests for all of the above (+7).

### Out of Scope

- NO serving-behavior change: identity, log provenance, and advisory printing are
  facts and text, not answers. The refusal contract, thresholds, and retrieval are
  untouched.
- NO new capability; NO change to the answer cache key (that is multi-manual's
  job).
- NO normalization/aliasing of historical `manual-actual` records (a miss stays a
  clean "no record", never a fuzzy match).

## Why one change

The four findings share one cause — the specs drifted from the shipped code — and
one verification story (hermetic suite + a re-synced spec tree). Splitting them
would multiply the artifact overhead without buying independence.

## Risks

| Risk | Mitigation |
|---|---|
| Web upload identity change moves calibration keys | The old key never matched a real manual; the new key is the same one the CLI and golden sets already use |
| `--record` note leaks into stdout | The note is written to stderr; a test pins stdout byte-identical to the no-flag run |
| An extra alias read per operator question (A2) | The revision is read once on the miss path (and reused from the cache path); eval traffic passes no log and pays nothing |

## Rollback

Revert the four source files and the spec deltas; the log's `revision` field
defaults to `""` for every existing reader, so old and new lines coexist.
