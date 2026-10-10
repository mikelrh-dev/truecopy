# Exploration: answer-cache-feedback

Change working name: `answer-cache-feedback`. Two independent improvements from
`MEJORAS-RAG.md` (items 1 and 2): an answer cache keyed by normalized question +
revision, and an operator feedback loop (thumbs UI + JSONL field + weekly
aggregation). This document answers the orchestrator's explore questions against
the real code.

## Current State

- `src/plant_rag/answer/pipeline.py::answer_question` is the single answering
  chain. Entrypoints: CLI (`scripts/query.py`), web (`web/app.py` via
  `Scheduler(_generate_and_record)`), eval (`scripts/eval.py`). The `log:
  QueryLog | None = None` parameter is the established "operator traffic only"
  seam: web and CLI pass a log, the eval passes none.
- Gates inside `_answer_question`: blank → `ValueError`; exact code-shaped
  question → `fastpath.lookup` (known → `alarm_answer`, unknown → exact
  `REFUSAL_ES`, zero LLM); else `retrieve_and_rank` → `llm.generate` (empty
  pool → exact refusal; safety topics get `SAFETY_WARNING_ES` prepended inside
  `generate` via `apply_safety_warning` BEFORE the `Answer` is returned).
- `models.Answer` is a frozen dataclass: `kind ∈ {answer, refusal, timeout}`,
  `text_es`, `citations`. Timeouts are built by the scheduler
  (`scheduler.timeout_answer`) from `DeadlineExceeded` — `answer_question`
  never returns a timeout, it raises.
- Corpus identity: ADR-001 blue/green revisions. `store.current_revision(client)`
  reads the `current_text` alias; `reindex.swap_aliases` swaps both aliases in
  ONE atomic Qdrant call. Mid-build the old revision keeps serving.
- `monitoring.py`: append-only JSONL; `QueryRecord` has fixed `_FIELDS`
  (ts, question, kind, latency_s, pages, manual_id, revision, model);
  `records()` builds records via `{k: data[k] for k in _FIELDS}` — a line
  missing any field raises `KeyError` and is skipped as corrupt. NOTE:
  `build_record` hardcodes `revision=""` today.
- Normalization precedent: `expand._normalize` and `prompt._normalize` are
  byte-identical (NFD → strip combining marks → casefold → collapse
  whitespace → strip). `expand.keyword_variant` strips Spanish stopwords to
  content words. `prompt.is_safety_query` uses a closed term set.
- Tests: 229 hermetic pytest tests (`.venv/Scripts/python.exe -m pytest tests
  -q`). `tests/conftest.py` autouse-fakes `pipeline.get_embedder` and
  `pipeline.rerank`. Web tests use `streamlit.testing.v1.AppTest`
  (`tests/test_owner_ops.py`).

## Affected Areas

- `src/plant_rag/answer/pipeline.py` — cache hook in `answer_question`
  (mirrors the `log` seam).
- `src/plant_rag/answer/cache.py` — NEW module (normalization, key, store).
- `src/plant_rag/monitoring.py` — feedback event record + tolerant read.
- `web/app.py` — thumbs widget near `_render_answer` / `_page_question`.
- `web/ui_es.py` — Spanish strings for the widget.
- `scripts/feedback_report.py` — NEW weekly aggregation script.
- `src/plant_rag/config.py` — `PLANT_RAG_*` knobs (cache path/enabled).
- Tests: `tests/test_pipeline.py`, `tests/test_monitoring.py`,
  `tests/test_web.py` (or `test_owner_ops.py`), NEW
  `tests/test_answer_cache.py`, NEW `tests/test_feedback_report.py`.

## Explore Questions

### 1. Cache key: normalization rules

Proposed key: `(revision, model, normalized_question)`.

Normalization of the question (reuse the `_normalize` precedent, plus
punctuation noise):

1. NFD-normalize, strip combining marks (accents), casefold.
2. Collapse internal whitespace runs to one space; strip ends.
3. Strip Spanish wrapping/terminal punctuation `¿ ¡ ? ! . , ; :` from both
   ends (operators type "¿Cuál es la presión?" and "cuál es la presión" —
   same question). Repeat until stable (nested "¿…?").

Deliberately NOT normalized, and why:

- **Word order / stopwords / plurals / synonyms** — that is semantic
  equivalence, and semantic equivalence is what retrieval already decides.
  A too-clever key merges questions whose correct answers could differ; the
  cache's job is only to erase typing noise, never meaning. Fewer hits, zero
  wrong hits.
- **Interior punctuation** ("arranque/parada" vs "arranque parada") — can
  change meaning; keep.
- **Digits** — alarm codes are identity; never touch them.
- **The ES→EN glossary expansion** — expansion is a retrieval aid applied
  per-revision; baking it into the key would couple cache hits to glossary
  edits without a revision bump.

`model` is in the key because an Ollama model swap changes answers without a
reindex. `revision` is in the key per the MEJORAS requirement.

### 2. Hook point options (cache)

| Option | Where | Pros | Cons |
|---|---|---|---|
| **A. Pipeline param (recommended)** | `answer_question(..., cache: AnswerCache \| None = None)`, check after the blank gate, before `_answer_question`; store on return | Mirrors the `log` seam exactly; ONE chain stays ONE chain; eval passes `cache=None` → eval traffic can neither hit nor pollute the operator cache; CLI and web get it by wiring only | Touches pipeline.py (small, additive) |
| B. Web layer | `_generate_and_record` in `web/app.py` | Zero pipeline change | Only web benefits; CLI spot-checks miss the cache; a second answering policy outside the chain (the exact drift the chain unification killed) |
| C. Query script | `scripts/query.py` | Zero pipeline change | CLI-only; same drift risk |

Per entrypoint under Option A:

- **CLI**: pass `AnswerCache.from_config()` — owner spot-checks repeat the
  same questions; latency ~0 on hits.
- **Web**: pass the same (process-lifetime instance resolved once, like
  `_query_log()`).
- **Eval**: passes nothing (same rule as `log`) — harness traffic never reads
  or writes the operator cache. This is a hard contract, stated in the
  `answer_question` docstring next to the `log` bullet.

Placement inside `answer_question`: hit → return cached `Answer` immediately
(still logged, latency ~0, so operator metrics stay truthful); miss → run the
chain, store the result. Cache only successful returns (`DeadlineExceeded`
and `LLMError` propagate and are never stored; timeout answers are scheduler
artifacts and never reach this layer). Refusals ARE stored — see risks.

### 3. Invalidation: why revision-scoping is sufficient

An answer is a pure function of `(question, revision, model, relevant
config)`. The key carries the first three, so a reindex (new revision) makes
every old key unreachable by construction — no TTL, no explicit flush, no
"clear cache" button. Mid-reindex:

- **During the build** (alias still on rN): the cache serves rN answers,
  exactly matching what retrieval would return. Consistent, not stale.
- **After the atomic swap** (rN+1): every lookup misses (key revision ≠
  current) → cold cache → full chain. Correct by construction.
- **The race**: a question resolves revision rN at entry, the swap lands
  mid-generation, the chain internally re-resolves aliases and may retrieve
  rN+1 evidence. Storing that answer under rN would poison an unreachable
  key (harmless — rN keys are never read again) BUT the operator saw an
  rN+1 answer already. Mitigation (cheap, recommended): re-read
  `current_revision` after the chain returns; store only if unchanged,
  else skip the store. One extra alias read per MISS only.

Remaining invalidation hole: config changes that alter answers without a
revision or model bump (`MIN_SCORE`, glossary, safety terms, prompt). v1
keeps the key simple and documents "config change → restart (in-memory
cache) or wipe cache dir"; a config fingerprint can be added to the key later
without breaking stored entries (old keys simply miss).

Storage recommendation: on-disk JSONL (one line per entry, keyed lookup via
an in-memory index built at first use, bounded LRU cap e.g. 1000 entries)
under a gitignored path (default `logs/answer-cache.jsonl`,
`PLANT_RAG_ANSWER_CACHE_PATH`, `""` disables). On-disk because the web
process may restart between identical shifts' questions and because it keeps
CLI and web sharing one cache; the file is best-effort like the query log —
a cache write failure never breaks an answer.

### 4. Feedback loop

**Widget placement (web/app.py):**

- Option A (recommended): thumbs row rendered right after the answer block,
  inside `_page_question` where `st.session_state["last_answer"]` is
  rendered — NOT inside `_render_answer` (which is a pure render function
  tested by source inspection; buttons add side effects). The widget shows
  only when `last_answer` is not None and no vote has been recorded for this
  answer (session flag `last_answer_voted`).
- Option B: inside `_render_answer` — keeps display and action together but
  breaks the "pure render" property and complicates the existing
  source-inspection test.
- Option C: sidebar — operators miss it; votes collapse.

Vote handling: on click, append a feedback event to the query log (best-effort,
same lock), set the session flag, disable/replace the buttons with a
confirmation caption. A new question clears the flag. Votes are allowed on
refusals too (a thumbs-down on a refusal is the single most valuable signal
for improvements 3 and 4 in MEJORAS). Voter identity is NOT recorded: the log
convention says it records facts about answers, never operator identity;
`user.code` stays in the session only.

**Schema extension (monitoring.py):**

The log is append-only, so the vote cannot be retrofitted onto the already
written answer line. The "new field" lands on a **feedback event line** in the
same `queries.jsonl`:

```json
{"ts": 1760000000.0, "event": "feedback", "question": "presion neumaticos",
 "vote": "down", "kind": "answer", "model": "qwen2.5:7b-instruct"}
```

- `QueryLog.record_feedback(question, kind, vote)` appends it under the same
  lock, best-effort.
- **Backward-compatible read (critical)**: `records()` currently does
  `{k: data[k] for k in _FIELDS}` — adding a field to `_FIELDS` would make
  every OLD line raise `KeyError` and be silently dropped. The read must be
  reworked: parse `event`/shape first; lines that look like answer records
  parse with `data.get(k, default)` for any NEW optional fields only
  (existing fields stay required); lines with `event == "feedback"` go to a
  separate `feedback_records()` reader; anything else is skipped as corrupt
  (current behavior preserved).
- `stats()` keeps counting answer records only, so cumulative metrics in the
  web are unaffected by feedback lines.

**Aggregation script (`scripts/feedback_report.py`):**

- Inputs: `logs/queries.jsonl` (path overridable by arg/env); window:
  `--days 7` (default) or `--week YYYY-Www`.
- Logic: read both record kinds via `QueryLog`; compute weekly totals by
  `kind`; refusals and timeouts grouped by **topic key** =
  `expand.keyword_variant(normalized question)` (deterministic, zero new
  deps, no LLM — the same content-word reduction already used for retrieval);
  thumbs-up/down counts per topic and per `kind`; top-N lists (refusals by
  frequency, most-downvoted questions).
- Outputs: human-readable table to stdout + `--json` flag writing
  `logs/feedback-report.json`. No new services, no DB, stdlib only.
- Exit code 0 always unless the log is unreadable (mirrors the monitoring
  "log failure never breaks the app" philosophy, inverted for a report tool:
  a report that cannot read its input must fail loudly).

### 5. Risks and mitigations

**Answer cache:**

| Risk | Mitigation |
|---|---|
| Caching a refusal that a config change (MIN_SCORE, glossary) would now answer | Documented wipe-on-config-change; optional config fingerprint in key later; feedback thumbs-down on refusals surfaces exactly this case for improvement 3 |
| Caching safety-warning answers | Safe by construction: the warning is baked into `text_es` at generation time by `apply_safety_warning`; replay preserves the contract string verbatim. No special-casing needed — state it in the module docstring |
| Mid-reindex poisoning | Re-check revision after the chain returns; store only when unchanged (miss-path cost: one alias read) |
| Multi-user staleness | None by design: ONE corpus for everyone (web `_generate_and_record` docstring already relies on "identical questions → identical answers"); the cache strengthens that consistency |
| Log/metrics distortion on hits | Hits are still logged with real (≈0) latency; metrics stay truthful. Optionally add `cached: bool` later — NOT in v1 to keep `_FIELDS` churn minimal |
| Cache file growth / corruption | Bounded LRU cap (default 1000 entries); read path skips corrupt lines exactly like `QueryLog`; write failures swallowed (cache must never break an answer) |
| Eval pollution | Contract: eval passes `cache=None` (mirrors `log=None`); test asserts the eval module never imports/constructs a cache |

**Feedback loop:**

| Risk | Mitigation |
|---|---|
| Old log lines dropped by a naive `_FIELDS` addition | Rework `records()` before adding fields (see §4); regression test: old-format line + new feedback line in one file, both read back correctly |
| Double votes / votes on stale answers | Session flag per rendered answer; cleared on each new question; buttons hidden after voting |
| Feedback lines skewing `stats()` / web cumulative metrics | Separate `feedback_records()` reader; `stats()` counts answer records only |
| Log growth | One ~200-byte line per vote; LAN scale (dozens of votes/day) is negligible; aggregation reads whole file once per run |
| Topic grouping quality | v1 uses deterministic keyword reduction (no LLM); the report is a triage aid for the owner, not an ML system — over-promising clustering would violate the local-only, no-magic conventions |

### 6. Test seams (hermetic coverage)

- **Cache**: inject a fake `AnswerCache` object exactly like `transport` and
  `log` (constructor param); pipeline tests monkeypatch the module-level
  names already documented in the pipeline docstring (`lookup`,
  `hybrid_search`, `get_embedder`, `rerank`, `llm_generate`) — the conftest
  autouse fixture already fakes embedder+rerank, so a cache test needs only a
  stub `client` and an in-memory cache. Revision re-check: monkeypatch the
  new module-level `current_revision` import in `pipeline` (same pattern as
  `get_embedder`). Normalization: pure-function tests on `cache.normalize`
  (accents, case, ¿…?, whitespace) with no I/O.
- **Feedback write**: extends `tests/test_monitoring.py` patterns (tmp_path
  JSONL): `record_feedback` round-trip, best-effort swallow, mixed old/new
  file read, `stats()` unaffected by feedback lines.
- **Widget**: `AppTest` patterns from `tests/test_owner_ops.py` (login → ask
  with a stubbed scheduler answer → click thumb → assert log line). Source
  inspection test (pattern from `test_web.py::_render_answer` check) can pin
  that the widget reads `ui_es` strings only.
- **Aggregation**: pure function over `list[QueryRecord]` + tmp_path JSONL —
  no live stack.

## Approaches (summary)

1. **Option A (recommended) — pipeline seam + new `answer/cache.py` +
   feedback events in queries.jsonl + `scripts/feedback_report.py`** —
   follows every established seam (`log`, module-level monkeypatch points,
   best-effort writes, `PLANT_RAG_*` config). Effort: Medium (two independent
   slices, each landable alone: cache first, feedback second, per MEJORAS
   sequence).
2. Web-only cache — rejected: drifts from the one-chain convention, CLI blind.
3. In-memory-only cache — rejected as sole store: dies with the process;
   shifts repeat across restarts. (An in-memory index over the JSONL file is
   still part of Option 1.)
4. Separate `feedback.jsonl` — viable alternative if the reviewer prefers
   zero risk to the existing reader; same schema otherwise. Slightly more
   file sprawl; MEJORAS says "campo en queries.jsonl", so same-file is the
   default recommendation.

## Recommendation

Proceed with Approach 1, delivered as two independent work slices:

- **Slice 1 (cache)**: `answer/cache.py` + `answer_question(cache=...)` +
  wiring in CLI/web + config + tests. Eval untouched by contract.
- **Slice 2 (feedback)**: `monitoring.record_feedback` + tolerant read
  rework + `web` widget + `ui_es` strings + `scripts/feedback_report.py` +
  tests.

Both slices stay under the 400-line review budget individually; together they
likely exceed it → chained PRs (delivery_strategy=auto-chain already cached).

## Ready for Proposal

Yes. The orchestrator can tell the user: exploration confirms both
improvements are cheap, independent, and land on existing seams; the two
non-obvious discoveries that shaped the design are (a) the `_FIELDS`
`data[k]` read drops old lines if extended naively, and (b) the mid-reindex
race requires a post-answer revision re-check before storing.
