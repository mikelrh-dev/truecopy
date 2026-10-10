# Tasks: Answer Cache + Operator Feedback Loop

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | Slice 1 ≈540, Slice 2 ≈660, total ≈1200 (budget 3000) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1 → PR 2, stacked chain |
| Delivery strategy | auto-chain |
| Chain strategy | feature-branch-chain (resolved by apply batch 1) |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Slice 1: answer cache (`answer/cache.py`, seam, config, CLI/web wiring, tests) | PR 1 | `.venv/Scripts/python.exe -m pytest tests/test_answer_cache.py tests/test_pipeline.py -q` | `python scripts/query.py "<q>"` twice; second hit ≈0 latency | `answer/cache.py`, pipeline seam, config knobs, wiring, `tests/test_answer_cache.py` |
| 2 | Slice 2: feedback (tolerant read first, votes, widget, report, tests) | PR 2 | `.venv/Scripts/python.exe -m pytest tests/test_feedback_report.py tests/test_monitoring.py tests/test_web.py -q` | Web thumbs vote: one recorded, control disables | Feedback code in `monitoring.py`, widget, `scripts/feedback_report.py`, tests; keeps `_answer_cache()` |

Units land alone; tests travel with code; revert PR 2 before PR 1.

## Phase 1: Slice 1 — Cache Foundation

- [x] 1.1 Create `src/plant_rag/answer/cache.py`: `normalize()` (NFD, de-accent, casefold, collapse whitespace, trim ES punctuation); table tests in `tests/test_answer_cache.py`.
- [x] 1.2 Add `ANSWER_CACHE_PATH` (default `logs/answer-cache.jsonl`, `""` disables) and `ANSWER_CACHE_MAX_ENTRIES=1000` to `src/plant_rag/config.py`; gitignore the cache file.
- [x] 1.3 Implement `AnswerCache` (append-only JSONL, LRU cap, tolerant load skipping corrupt records, `from_config()` None when disabled); tests: round-trip, LRU cap, corrupt skip, best-effort store failure.

## Phase 2: Slice 1 — Pipeline Seam

- [x] 2.1 Add `cache=None` to `answer_question` (`src/plant_rag/answer/pipeline.py`) after blank gate, mirroring `log`; module-level `current_revision` import; tests patch `pipeline.current_revision`.
- [x] 2.2 Hit: stored `Answer` byte-identical, logged ≈0 latency. Miss only: run chain, re-read `current_revision`, store iff unchanged; `DeadlineExceeded`/`LLMError` propagate pre-store; refusals stored.
- [x] 2.3 Tests: repeat/normalized hit, revision+model miss, mid-answer swap skips store, timeout never stored, silent store failure, eval isolation (`cache=None` never reads/writes; `scripts/eval.py` unmodified).

## Phase 3: Slice 1 — Wiring and Land

- [x] 3.1 `scripts/query.py`: pass `cache=AnswerCache.from_config()`; `web/app.py`: `_answer_cache()` per process mirroring `_query_log()`, passed to `answer_question`.
- [x] 3.2 Run `.venv/Scripts/python.exe -m pytest tests -q`; commit Slice 1 with its tests.

## Phase 4: Slice 2 — Tolerant Read (FIRST committable unit)

- [x] 4.1 Rework `records()` in `src/plant_rag/monitoring.py`: skip `event=="feedback"` lines before any `_FIELDS` access; never extend `_FIELDS` naively.
- [x] 4.2 Tests: pre-change lines read unchanged; mixed log returns answers only; `stats()` ignores votes. Commit 4.1+tests before any commit writes feedback lines.

## Phase 5: Slice 2 — Feedback Write Path

- [x] 5.1 Add `FeedbackRecord`, `record_feedback(...)` (`vote ∈ {"up","down"}`, `{"event":"feedback"}` line under existing lock, best-effort), `feedback_records()` to `monitoring.py`.
- [x] 5.2 Tests: feedback readable (question/kind/vote); invalid vote rejected; write failure never raises.

## Phase 6: Slice 2 — Thumbs Widget

- [x] 6.1 `web/ui_es.py`: add `FEEDBACK_THUMBS_UP/DOWN`, `FEEDBACK_DONE`, `FEEDBACK_LABEL`.
- [x] 6.2 `web/app.py`: widget in `_page_question` after `_render_answer` for `kind ∈ {answer, refusal}` (timeouts excluded); vote → best-effort `record_feedback` → `last_answer_voted=True`, disabled; new question resets.
- [x] 6.3 Tests (`tests/test_web.py`, `tests/test_owner_ops.py`): vote once, disabled after, per-question reset, timeout not rateable.

## Phase 7: Slice 2 — Weekly Report and Land

- [x] 7.1 Create stdlib `scripts/feedback_report.py`: `--log`, `--days` (7), `--top` (10), `--json`; pure `topic_key`/`aggregate`/`format_table`; `ts >= now - days*86400`; down-count desc, ties alphabetical; unreadable log → stderr, exit 1.
- [x] 7.2 `tests/test_feedback_report.py`: default window/top-10, flag overrides, fail-loud unreadable log, deterministic ordering.
- [x] 7.3 Run `.venv/Scripts/python.exe -m pytest tests -q`; commit Slice 2 with tests (revertible before Slice 1).
