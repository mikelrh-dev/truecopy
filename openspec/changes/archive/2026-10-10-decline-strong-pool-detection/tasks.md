# Tasks: Decline-strong-pool detection

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~180 (code ~90, tests ~90) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, 2 revertible work-unit commits |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: Low

Open question resolved: the event lives in the operator query log as a third
schema (no new file), and the second-decline path is included because the pool is
unchanged between attempts. Phase 2 (directed retry) is explicitly deferred.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | Event schema + report view | `.venv/Scripts/python.exe -m pytest tests/test_monitoring.py tests/test_feedback_report.py -q` | Revert `monitoring.py` + `feedback_report.py` hunks |
| 2 | Signal seam + second decline | `.venv/Scripts/python.exe -m pytest tests/test_answer.py tests/test_pipeline.py -q` | Revert `llm.py` + `pipeline.py` hunks |

## Phase 1: Event schema (`src/plant_rag/monitoring.py`)

- [x] 1.1 Add `DeclineSignalRecord` (ts, question, pool_size, pages, manual_id, revision, model) with `to_json()` writing `"event": "decline-strong-pool"`
- [x] 1.2 Add `QueryLog.record_decline_signal(question, hits, *, model)` — best-effort write under the shared lock, never raises
- [x] 1.3 Add `QueryLog.event_records(event=None)` returning parseable event lines, filterable by type
- [x] 1.4 `records()` branches on ANY `event` key before `_FIELDS` access, so a future event type cannot be misread as a corrupt answer
- [x] 1.5 Tests: event round-trip with its pool; write-failure-never-raises; answer stats ignore event lines

## Phase 2: Signal seam (`src/plant_rag/answer/llm.py`, `src/plant_rag/answer/pipeline.py`)

- [x] 2.1 `llm.generate` accepts optional `decline_signal: Callable[[str, tuple[Hit, ...]], None]`; `_full_pool(hits)` is `len(hits) >= config.TOP_K`
- [x] 2.2 Invoke the signal on the two first-reply decline paths (explicit refusal text; self-decline with no citation attempted) when the pool is full
- [x] 2.3 Invoke the signal on the retry's refusal too (second decline, pool unchanged)
- [x] 2.4 `pipeline._answer_question` forwards `decline_signal`; `answer_question` binds it to the operator log only, so eval traffic emits none
- [x] 2.5 Tests: first-decline-with-full-pool records one signal; second-decline-after-retry records one signal and still runs the retry once; thin pool records none; no-log traffic forwards `decline_signal=None`; the exact refusal is preserved

## Phase 3: Detection view (`scripts/feedback_report.py`)

- [x] 3.1 `--signals` flag selects the decline view (same `--days`/`--top`/`--log` contract)
- [x] 3.2 `aggregate_signals` + `format_signals`: per-topic count (desc, alphabetical ties) with pool size and pages of the newest occurrence
- [x] 3.3 Unreadable log fails loudly (exit 1); empty window is still a report (exit 0)
- [x] 3.4 Tests: aggregation window/ordering; pool size and pages of the newest occurrence; fail-loud on unreadable log

## Phase 4: Verification

- [x] 4.1 Full suite `.venv/Scripts/python.exe -m pytest tests -q` green
- [x] 4.2 The signal is bound only when an operator log exists (pinned by `test_traffic_without_a_log_gets_no_decline_signal`)
- [x] 4.3 No serving-side reader consumes the event; the answer contract is unchanged
