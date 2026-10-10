# Design: Answer Cache + Operator Feedback Loop

## Technical Approach

Option A, two landable slices. Slice 1 adds `answer/cache.py` and a `cache=` seam on `answer_question` mirroring `log`. Slice 2 reworks `monitoring.py` tolerant reads, feedback event lines, the thumbs widget, and `scripts/feedback_report.py`. Seams reused: module-level monkeypatch names, injection params, best-effort IO, stdlib only.

### Hard sequencing constraints

1. Slice 1 (cache) lands before Slice 2 (feedback), chained PRs (auto-chain).
2. In Slice 2 the `records()` tolerant-read rework is the FIRST commit: no feedback line is written before every reader tolerates it.
3. The revision re-check runs on the miss path only; hits pay zero extra alias reads.

## Architecture Decisions

| Topic | Chosen | Rejected (why) |
|---|---|---|
| Cache key | `(revision, model, normalize(question))` — reindex/model swap invalidates by construction | TTL/flush UI (out of scope); config fingerprint (deferred; documented wipe) |
| Hook | `answer_question(..., cache=None)` after the blank gate, mirroring `log` | Web-layer-only cache (second policy, CLI blind) |
| Store | gitignored `logs/answer-cache.jsonl` + in-memory LRU (cap `ANSWER_CACHE_MAX_ENTRIES=1000`), best-effort | SQLite (new dep); memory-only (dies with process) |
| Race | Re-read `current_revision` after miss chain; store only if unchanged | No check (mixed-revision poisoning); locking (cost) |
| Feedback storage | `{"event":"feedback",...}` lines in `queries.jsonl`, same lock, `FeedbackRecord` | Naive `_FIELDS` extension (KeyError drops old lines); separate file (MEJORAS: queries.jsonl) |
| Reader | Branch on `data.get("event")` before any `_FIELDS` access; `feedback_records()` separate | Shared parser (couples schemas) |
| Widget | `_page_question` after `_render_answer`; flag `last_answer_voted` | Inside `_render_answer` (pure-render, source-inspection pinned) |
| Topics | `keyword_variant(q) or normalize(q)` — deterministic | LLM/clustering (non-deterministic, out of scope) |

## Data Flow

```
CLI/web ─question─► answer_question(cache, log)
                      │ blank gate → ValueError (unchanged)
                      │ lookup(revision, model, normalize(q)) ──► AnswerCache (LRU+JSONL)
                      │   hit  → return stored Answer; log real ≈0s latency
                      │   miss → chain; current_revision re-check (miss only);
                      │          store iff unchanged (best-effort)
Eval ──► answer_question(...)   # cache=None: never reads, never writes
```

## File Changes

| File | Action | Description |
|---|---|---|
| `src/plant_rag/answer/cache.py` | Create | `normalize`, key, `AnswerCache` (JSONL + LRU, tolerant load) |
| `src/plant_rag/answer/pipeline.py` | Modify | `cache=` param, hit/miss hook, module-level `current_revision` import |
| `src/plant_rag/config.py` | Modify | `ANSWER_CACHE_PATH` (default `logs/answer-cache.jsonl`, `""` disables), `ANSWER_CACHE_MAX_ENTRIES` (1000) |
| `scripts/query.py` | Modify | Pass `cache=AnswerCache.from_config()` |
| `web/app.py` | Modify | `_answer_cache()` per process (mirrors `_query_log()`); widget + session flags |
| `web/ui_es.py` | Modify | `FEEDBACK_THUMBS_UP/DOWN`, `FEEDBACK_DONE`, `FEEDBACK_LABEL` |
| `src/plant_rag/monitoring.py` | Modify | Tolerant `records()`; `FeedbackRecord`; `record_feedback`; `feedback_records` |
| `scripts/feedback_report.py` | Create | Stdlib weekly triage report |
| `tests/test_answer_cache.py`, `tests/test_feedback_report.py` | Create | New suites |
| `tests/test_pipeline.py`, `test_monitoring.py`, `test_web.py`/`test_owner_ops.py` | Modify | Hook, mixed-log, widget coverage |

## Interfaces / Contracts

```python
def normalize(question: str) -> str: ...  # NFD, accents off, casefold, collapse ws, strip ES punctuation both ends
class AnswerCache:
    @classmethod
    def from_config(cls) -> AnswerCache | None: ...      # None when disabled
    def lookup(self, revision: int | None, model: str, question: str) -> Answer | None: ...
    def store(self, revision: int | None, model: str, question: str, answer: Answer) -> None: ...
```

Pipeline: `answer_question(..., log=None, cache=None)`. With cache: resolve client once, `revision = current_revision(qdrant)` (module-level import → tests patch `pipeline.current_revision`, like `get_embedder`). Hit → cached `Answer`, still logged at ≈0 latency (no `cached` field in `_FIELDS`). Miss → chain; `DeadlineExceeded`/`LLMError` propagate before any store; post-answer revision compare decides the store. Refusals/safety stored verbatim (warning baked into `text_es`). Entry: raw question, kind, text_es, citations `[[manual_id, page],...]`; corrupt lines skipped; index rebuilt on load, trimmed to newest cap; file never rewritten.

Monitoring: `record_feedback(question, kind, vote, *, model)` validates `vote ∈ {"up","down"}`, appends under the existing lock, best-effort. `records()` skips `event=="feedback"` lines before `_FIELDS`; `feedback_records()` parses only those; `stats()` unchanged — isolation by construction.

Web: vote → best-effort `record_feedback` → `last_answer_voted=True` + confirmation, control disabled; new question resets the flag and `last_answer_question`; widget only for `kind in {"answer","refusal"}` (timeouts not rateable).

Report: flags `--log`, `--days` (7), `--top` (10), `--json`. Pure seams `topic_key`, `aggregate(records, *, now, days, top)`, `format_table`. Window `ts >= now - days*86400`; topics by down-count desc, ties alphabetical; unreadable log → stderr + exit 1. `--week` deferred.

## Testing Strategy

| Layer | What | Approach |
|---|---|---|
| Unit | `normalize`, `topic_key` | Table-driven: accents, ¿…?, whitespace |
| Unit | Hit/miss, LRU cap, corrupt skip, best-effort store | `tmp_path` JSONL; fake cache like `transport` |
| Unit | ≈0s hit log; revision swap skips store; timeout never stored | Patch `pipeline.current_revision` |
| Unit | Eval isolation (`cache=None` never reads/writes) | Assert no cache construction without param |
| Unit | Mixed log; stats ignore votes; report defaults/fail-loud | `tmp_path` + pure `aggregate` |
| Integration | Vote once, disable, per-question reset | `AppTest` (test_owner_ops pattern) |

Suite stays hermetic: 229+ tests via `.venv/Scripts/python.exe -m pytest tests -q`.

## Threat Matrix

N/A — no routing, shell, subprocesses, VCS/PR automation, executable-file classification, or process-integration boundary. Persistence is append-only JSONL via stdlib; no new services.

## Migration / Rollout

No migration. Cache file is disposable gitignored data (ensure `.gitignore` covers it). Config changes without revision/model bump: documented wipe in the cache docstring; fingerprint deferred. Rollback: revert chained PRs in reverse; pre-change readers already skip feedback lines via the KeyError-as-corrupt path.

## Open Questions

None blocking. Deferred by decision: `--week` report flag, config fingerprint (additive later, no v1 breakage).
