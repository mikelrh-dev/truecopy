# Tasks: Post-audit hardening

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~300 (code ~110, tests ~190) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, 4 revertible work-unit commits |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: Low

Open question resolved: the identity is the sanitized filename (not the UUID
staged path), and historical `manual-actual` records are left alone (a miss stays
a clean "no record").

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | One identity namespace | `.venv/Scripts/python.exe -m pytest tests/test_owner_ops.py tests/test_ingest.py -q` | Revert the helper + CLI/web hunks |
| 2 | Log provenance | `.venv/Scripts/python.exe -m pytest tests/test_pipeline.py -q` | Revert `monitoring.py` + `pipeline.py` hunks |
| 3 | Reindex-CLI advisory | `.venv/Scripts/python.exe -m pytest tests/test_ingest.py -q` | Revert the `scripts/reindex.py` advisory hunk |
| 4 | Eval `--record` diagnostics | `.venv/Scripts/python.exe -m pytest tests/test_scripts.py -q` | Revert the `scripts/eval.py` hunk |

## Phase 1: One identity namespace

- [x] 1.1 RED: `tests/test_owner_ops.py` — two uploads get two ids; the filename never reaches the staged path (traversal stays in the staging dir)
- [x] 1.2 GREEN: add `manual_id_from_filename()` to `ingest/reindex.py` (stdlib `re`, sanitize to `[0-9A-Za-z_]`, fallback `"manual-sin-nombre"`)
- [x] 1.3 Wire `scripts/ingest.py`, `scripts/reindex.py`, and `web/app.py::_do_upload` to the helper; update the existing web assertion

## Phase 2: Log provenance

- [x] 2.1 `monitoring.build_record(..., revision="")` — parameter with historical default, no field added
- [x] 2.2 `pipeline.answer_question` resolves `current_revision` once when a log exists and stamps `_revision_tag` on both the hit and the miss paths
- [x] 2.3 RED+GREEN: `tests/test_pipeline.py` — a logged answer records `rN`; a cached hit records the same revision

## Phase 3: Reindex-CLI advisory

- [x] 3.1 RED: `tests/test_ingest.py` — `scripts/reindex.py` prints the same advisory after the swap summary
- [x] 3.2 GREEN: call `calibration.advisory(config.CALIBRATION_PATH, manual_id, result.revision, cache_path=...)` and print when non-None

## Phase 4: Eval `--record` diagnostics

- [x] 4.1 RED: `tests/test_scripts.py` — `--record` that appends nothing prints the reason on stderr and stdout stays byte-identical
- [x] 4.2 GREEN: after the append branch, print the Spanish reason on stderr (red checks / empty sweep / disabled path)

## Phase 5: Verification

- [x] 5.1 Full suite `.venv/Scripts/python.exe -m pytest tests -q` green
- [x] 5.2 Specs re-synced: `manual-ingestion`, `min-score-calibration`, `answering`
- [x] 5.3 No serving-behavior change: the refusal contract and thresholds are untouched
