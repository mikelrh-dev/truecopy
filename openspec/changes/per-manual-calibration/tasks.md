# Tasks: Per-manual calibration

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~700 (600–800: code ~330, tests ~370) |
| 400-line budget risk | Medium — exceeds 400 raw, well under 3000 |
| Chained PRs recommended | No |
| Suggested split | Single PR, 4 revertible work-unit commits |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch-chain
400-line budget risk: Medium

Open question resolved: only `scripts/ingest.py` hosts the v1 advisory; `scripts/reindex.py` wiring is a deferred follow-up, not a task. Tests travel with code; stdlib only; local-only.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------------------|-----------------|-------------------|
| 1 | Record model + tests | 1 | `.venv/Scripts/python.exe -m pytest tests/test_calibration.py -q` | N/A: stdlib JSONL, no process boundary | Delete `calibration.py` + its tests |
| 2 | Config knob | 1 | `.venv/Scripts/python.exe -m pytest tests -q -k CALIBRATION` | N/A: env config only | Revert `config.py` hunk |
| 3 | Eval `--record` + tests | 1 | `.venv/Scripts/python.exe -m pytest tests/test_scripts.py -q` | N/A: hermetic CLI, pinned tests | Revert `scripts/eval.py` + test hunks |
| 4 | Ingest advisory + guard + tests | 1 | `.venv/Scripts/python.exe -m pytest tests -q` | N/A: prints only | Revert `scripts/ingest.py` + guard test |

## Phase 1: Record model (`src/plant_rag/calibration.py`)

- [x] 1.1 RED: in new `tests/test_calibration.py` write round-trip, append-never-rewrites, latest-wins, corrupt-line-skip tests against the design interface
- [x] 1.2 GREEN: create `calibration.py` with pure `build_record()` (v1 schema) and `append_record()`/`read_records()`/`latest_record()` — stdlib, `""` path no-op, mkdir parents, `ensure_ascii=False`, ISO `+00:00`
- [x] 1.3 RED+GREEN: staleness matrix tests (revision mismatch, label drift = hard; >90d = soft; else fresh) then `staleness()` with `current_label_totals`/`now` params
- [x] 1.4 RED+GREEN: `suggest()`/`advisory()` contract tests (floors, revision, date, counts, label, token `separates this labeled set`, wipe instruction iff floors differ, `None` iff path empty) then Spanish implementation
- [x] 1.5 Dense-branch-only docstring (text branch ungated) + `__doc__` substring test; "optimal" never appears

## Phase 2: Config knob (`src/plant_rag/config.py`)

- [x] 2.1 Add `CALIBRATION_PATH` via `_env_str` (default `logs/calibration.jsonl`, `""` disables), new end-of-file section mirroring `QUERY_LOG_PATH`/`ANSWER_CACHE_PATH`

## Phase 3: Eval wiring (`scripts/eval.py`)

- [x] 3.1 RED: in `tests/test_scripts.py` add — no flag writes nothing; `--record` appends one line, `config.MIN_SCORE` unchanged, report-only line prints; `--record`+`--no-calibration` exits 2; empty path silent with unchanged checks/exit codes
- [x] 3.2 GREEN: additive `--record` flag; optional `golden_pass`/`golden_total` (default `None`, `__str__` byte-identical); `_best_separating()` extraction (output unchanged); append only after all checks pass; source asserts keep `cache=`/`AnswerCache` absent

## Phase 4: Ingest advisory (`scripts/ingest.py`)

- [x] 4.1 RED: in `tests/test_ingest.py` add fresh/no-record/stale/disabled cases via monkeypatched reindex; assert index, config, store unchanged
- [x] 4.2 GREEN: after reindex summary, print read-only Spanish advisory from `advisory(path, id, revision, cache_path=...)`; empty path prints nothing; web path untouched

## Phase 5: Guard and verification

- [x] 5.1 Anti-import guard: source-scan `retrieve/` and `answer/` asserting neither imports `plant_rag.calibration` (precedent: `tests/test_pipeline.py`)
- [x] 5.2 Full suite `.venv/Scripts/python.exe -m pytest tests -q` green; eval contract unchanged (checks, lines, exit codes)
