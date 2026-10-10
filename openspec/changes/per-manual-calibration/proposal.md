# Proposal: Per-manual calibration

## Intent

MEJORAS-RAG.md item 3: persist the recommended floor + golden counts per `manual_id`/revision, suggest at ingest, owner applies. Eval informs; no silent automatism.

## Scope

### In Scope
- `CALIBRATION_PATH`, default `logs/calibration.jsonl` (env-overridable; `""` disables).
- New stdlib-only `src/plant_rag/calibration.py`: append-only JSONL v1, latest-wins read, staleness (revision/label-drift hard, age soft), `suggest()`.
- `scripts/eval.py`: additive `--record` + structured golden counts; checks, exit codes, output unchanged.
- `scripts/ingest.py`: Spanish suggestion via shared helper (web untouched, seam kept).
- Anti-import test: `retrieve/`+`answer/` never import `calibration.py`.
- Text branch ungated, documented dense-branch-only; serving precedence untouched: env > default, records never participate.

### Out of Scope
- Hybrid-path sweep, `TEXT_MIN_SCORE`, web surface, `manual_id` normalization, feedback coupling, any serving/refusal change.

**Decision (was open)**: records keyed by ingested id verbatim — CLI PDF-stem and web `manual-actual` are separate keys; a miss fails clean to "no record", never fuzzy-match; normalization deferred, documented.

## Capabilities

### New Capabilities
- `min-score-calibration`: record schema, staleness, latest-wins, ingest suggestion, serving non-participation.

### Modified Capabilities
- `answering`: eval adds `--record` + golden counts; report-only line preserved.
- `manual-ingestion`: reindex prints a read-only advisory suggestion.

## Approach

Approach 2: pure `build_record()` without I/O; hermetic tests; suggestions carry revision, date, counts; wording "separates this labeled set".

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/plant_rag/calibration.py` | New | records, staleness, suggest() |
| `src/plant_rag/config.py` | Modified | `CALIBRATION_PATH` |
| `scripts/eval.py` | Modified | `--record`, golden counts |
| `scripts/ingest.py` | Modified | suggestion print |
| `tests/test_calibration.py`, ingest/scripts tests | New/Modified | contracts, anti-import guard |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Auto-apply (serving reads record) | Med | anti-import test; precedence untouched |
| Stale record as truth | Med | downgrade-not-hide; counts+date each line |
| Eval contract drift | Low | additive only; pinned `AnswerCache` test |
| Floor change, stale cache | Med | suggestion prints cache-wipe instruction |

## Rejected alternatives

- Report-only: fails item 3.
- Full subsystem: too many knobs for one operator.
- config-dict: data is not config; breaks flat env contract.
- Qdrant store: couples reports to serving DB.
- OpenSpec/Engram: wrong layer — SDD, not runtime data.

## Rollback Plan

Revert config key, eval flag, ingest print; delete `calibration.py` and the JSONL. Serving never changed.

## Dependencies

- Exploration: `explore.md`, Engram 2813.

## Success Criteria

- [ ] `pytest tests` green including the anti-import guard.
- [ ] `--record` appends one line, never mutates `config.MIN_SCORE`.
- [ ] Ingest prints record/no-record/stale variants; empty path silent.
- [ ] Eval checks, lines, exit codes unchanged.

## Proposal question round

Assumptions: gitignored `logs/` store; verbatim id keys; ungated text branch; web deferred.
