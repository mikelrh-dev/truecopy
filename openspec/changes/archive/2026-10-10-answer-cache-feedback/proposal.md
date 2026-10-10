# Proposal: Answer Cache + Operator Feedback Loop

## Intent

MEJORAS-RAG.md 1+2: shifts repeat the same questions (latency, LLM cost) and improvements run blind. Add a revision-scoped answer cache and a thumbs-feedback loop with weekly aggregation.

## Scope

### In Scope
- **Slice 1 (cache)**: new `src/plant_rag/answer/cache.py` (key, JSONL + LRU index); `answer_question(..., cache=None)` seam mirroring `log`; CLI/web wiring; `PLANT_RAG_ANSWER_CACHE_*` knobs; tests. Eval passes `cache=None`: no read, no pollution.
- **Slice 2 (feedback)**: `QueryLog.record_feedback` event lines in `queries.jsonl`; tolerant `records()` rework FIRST (never extend `_FIELDS` naively); `feedback_records()`; thumbs widget in `_page_question` + `ui_es.py`; `scripts/feedback_report.py` (stdlib only); tests.

### Out of Scope
No TTL/flush UI; no config fingerprint in v1 (documented wipe-on-config-change); no voter identity; no LLM in aggregation; no `cached` field in `_FIELDS`; stale `tests/conftest.py:10` docstring naming missing `tests/test_rerank.py`.

## Capabilities

### New Capabilities
- `answer-cache`: revision + model + normalized-question key; seam; eval isolation; best-effort store; reindex invalidation; post-answer revision re-check.
- `operator-feedback`: thumbs widget, feedback event lines, tolerant read, stats isolation, weekly report.

### Modified Capabilities
- `answering`: evaluation set SHALL run with the cache disabled.

## Approach

Exploration Option A. Hit → return the cached `Answer`, logged at real ≈0 latency. Miss → run chain, re-read `current_revision`, store only if unchanged. Refusals and safety answers stored (warning baked into `text_es`); timeouts/LLM errors never. Store: gitignored `logs/answer-cache.jsonl`, LRU ~1000, best-effort. Votes append `{"event":"feedback"}` under the log's lock; `stats()` counts answer records only.

## Affected Areas

| Area | Impact |
|------|--------|
| `answer/pipeline.py`, `answer/cache.py` (new), `config.py`, `scripts/query.py` | New/Modified (S1) |
| `monitoring.py`, `web/app.py`, `web/ui_es.py`, `scripts/feedback_report.py` (new) | Modified/New (S2) |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Mid-reindex race stores a mixed-revision answer | Low | Post-answer revision re-check; skip store |
| Naive `_FIELDS` extension drops every old log line | Medium | Rework `records()` first; mixed-format regression test |
| Cached refusal outlives a config change | Medium | Documented wipe; thumbs-down surfaces it; fingerprint deferred |
| Eval traffic pollutes the operator cache | Low | `cache=None` contract + eval-never-caches test |

## Alternatives Rejected

- Web-only cache — second answering policy outside the chain; CLI blind.
- Memory-only store — dies with the process; shifts span restarts.

## Rollback Plan

Revert slice PRs in reverse order. Pre-change readers skip feedback event lines via the existing KeyError-as-corrupt path; the cache file is disposable gitignored data.

## Dependencies

- ADR-001 aliases; existing `log` seam; no new packages.

## Success Criteria

- [ ] Repeat question hits at ≈0 latency, logged truthfully; eval never touches the cache.
- [ ] Reindex makes every key miss; mixed old/feedback lines both read; `stats()` ignores votes.
- [ ] Hermetic suite (229+ tests) green.
