# Complete Improvement Plan — TrueCopy RAG (spec)

Status: spec for maintainer review. No implementation approved. Nothing here
may be implemented until the maintainer approves this document. A separate
executor agent will implement approved slices later.

Related parked proposals (not superseded, coordinated below):

- `openspec/changes/2026-10-11-agnostic-alarm-codes/proposal.md` (parked)
- `openspec/changes/2026-10-11-eval-metrics-track/proposal.md` (parked)

Prior art already in the tree (do not rebuild):

- Answer cache keyed `(revision, model, config fingerprint,
  normalize(question))` — `src/plant_rag/answer/cache.py`
- Operator feedback votes + `decline-strong-pool` signals in query JSONL —
  `src/plant_rag/monitoring.py` (`record_feedback`, `record_decline_signal`)
- Reranker fallback is LOUD via `logging` warning (E4/M6) —
  `src/plant_rag/retrieve/rerank.py`
- Eval harness: 4 checks (alarm retrieval, cross-lingual ES→EN, refusal
  out-of-corpus, golden grounding) + `MIN_SCORE` sweep + `expect_alarms`
  per-manual flag — `scripts/eval.py`
- Calibration records: append-only JSONL, `latest_record()` per `manual_id`,
  read-only `advisory()` — `src/plant_rag/calibration.py`
- Alarm shape contract `^\d{4,6}$` shared by ingest and retrieval;
  `has_alarm_table()` treats an empty alarm collection as "no table" —
  `src/plant_rag/retrieve/fastpath.py`
- ES→EN glossary expansion (`expand_query`, additive and identity-safe) —
  `src/plant_rag/retrieve/expand.py`

All anchor points below were verified against the tree on 2026-10-12. Line
numbers may drift; symbols are the stable reference.

## Global safety rules (non-negotiable, apply to every phase)

1. The decline *decision* logic is frozen. Annotations may explain a decline
   that already happened; they may never change whether the system declines.
   (`answer_question()` in `src/plant_rag/answer/pipeline.py`; refusal
   constructor `refusal()`.)
2. Offline/study work (Phases 1 slices, Phase 3) is read-only on serving
   inputs (Qdrant read, goldens, query JSONL, calibration records) and writes
   to `outputs/` only. No changes to `src/plant_rag/answer/pipeline.py`,
   retrieval, or serving.
3. New query-log fields go through `_OPTIONAL_FIELDS` in
   `src/plant_rag/monitoring.py`, never `_FIELDS` — promoting to `_FIELDS`
   breaks every historical line on read (`records()` projects strictly).
4. Cache identity stays `(revision, model, fingerprint,
   normalize(original_question))`. Anything that rewrites the question must
   run BEFORE the cache lookup and must key on the original, never on a
   generated variant.
5. The alarm shape contract (`_ALARM_CODE_RE` in `fastpath.py`, ingestion
   rule in `ingest/alarms.py`) is the single source of truth until the
   parked agnostic-alarm-codes proposal is approved. No phase may fork it.

## Phase 0 — Corpus watchdog (idea 3, independent, first)

Goal: the system must never silently serve from a dead corpus again
(incident: zero-norm vectors → cosine 0.0 → empty pool → exact refusal).

- Periodic read-only check: vector norms per `manual_*_text` collection,
  point counts per collection, `current_*` aliases resolving to live
  collections.
- Core rule: distinguish "dormant by design" (alarm collection with 0
  points while `has_alarm_table()` is `False` → OK) from "dead"
  (zero-norm vectors, unresolvable alias → alert).
- Output: operator alert (log warning + optional UI badge hook; no serving
  change).
- Acceptance: seeded zero-norm fixture triggers the alert; Tesla-shaped
  empty alarm collections do not.
- Touches: new module + scheduling hook (`src/plant_rag/scheduling/`).
  No serving-path edits.

## Phase 1 — Measurement base (parked metrics slices 1–2 + idea 4)

Goal: every later serving change proves itself against numbers.

- Implement parked track slices in order:
  1. Hit Rate@K + MRR on hand-verified goldens (record rank, not pass/fail).
  2. P@K/R@K on a 20–30 query hand-labeled set with written relevance
     criteria.
- Regression gate in CI (idea 4): runs the 4 `scripts/eval.py` checks plus
  slices 1–2 against an ephemeral Qdrant; PRs fail on regression.
  Goldens must cover code-shaped queries with `expect_alarms` set (the
  eval already models `expect_alarms=False`; extend sets, not schema).
- Acceptance: gate green on HEAD; a synthetic retrieval regression
  (e.g. lowered floor) turns it red.
- Resolves parked-track open question 1 as a side effect (where rank
  lives: decided during slice 1 implementation, recorded here).

## Phase 2 — Answering path (ideas 1, 7, 2)

### 2a. Query rewriting (idea 1)

- A rewriter produces 2–3 variants fused by RRF; triggers only on poor
  queries (very short, alarm code glued to text, ES with EN terms).
- Contracts:
  - Must preserve code shapes: never deform `^\d{4,6}$` matches or
    alphanumeric alert codes. If the parked agnostic proposal lands, the
    rewriter reads the current revision's pattern instead of the hardcoded
    regex.
  - Must compose with, not replace, `expand_query()` (glossary expansion
    stays; rewriter output feeds through it).
  - Runs BEFORE the answer-cache lookup; cache keys use the original
    question (rule 4).
- Proof: offline eval with Phase-1 metrics before any serving change;
  ship only on measured Hit Rate@K gain.
- Acceptance: +Δ Hit Rate@K on goldens with no refusal-check regression;
  code-shaped queries byte-identical through the rewriter.

### 2b. Decline explanations (idea 7)

- When the pipeline declines, append a *why*: "code-shaped but no alarm
  table in this manual" vs "out of corpus" vs "weak pool". Reuses
  `decline-strong-pool` signals already recorded.
- Contract: decision logic untouched (rule 1); the parked agnostic
  proposal, once approved, enriches the alarm-absence message.
- Acceptance: eval check 3 (refusal out-of-corpus) still passes byte-identical
  on decisions; every decline carries a reason string covered by tests.

### 2c. Reranker-fallback surfacing (idea 2)

- Surface the existing LOUD fallback (E4/M6 log warning in `rerank.py`)
  as an answer annotation ("unranked order, retrieval only").
- Contract: eval check 4 (golden grounding) must tolerate the annotation;
  pin this in golden fixtures.
- Acceptance: forced scorer failure yields annotated answers; normal path
  byte-identical.

## Phase 3 — Offline study (ideas 5, 6 as parked-track slices 5–6)

- Slice 5: index a sample with 2–3 open embedding models, compare Hit
  Rate@K on goldens (dimensionality/language trade-offs on real data).
- Slice 6: reindex one Tesla section with 2 chunking strategies
  (size/overlap), compare recall.
- Both obey rule 2 (read-only inputs, `outputs/` only). Zero serving risk.
- Acceptance: reports in `outputs/` with method + numbers + conclusion;
  no `src/` diff.

## Phase 4 — Alarms + operator (parked agnostic proposal + idea 8)

- Unpark `2026-10-11-agnostic-alarm-codes` together with the per-manual
  calibration view (idea 8): the view reads calibration records
  (`latest_record()` per `manual_id`: floor, golden pass, last reindex)
  and hosts the pattern-confirmation step the parked proposal leaves
  open (inform/apply split, same as calibration — no silent automation).
- The view also answers parked-proposal open questions 2–3 (where the
  pattern lives; confirmation UI placement) — decided here, recorded in
  the unparked design.
- Acceptance: owner can confirm a per-revision pattern from the view;
  fast path reads it; Tesla collections stay exact-path-dormant with an
  explicit "no table" state instead of silent absence.

## Execution order and dependencies

Phase 0 → Phase 1 → Phase 2 (2a needs Phase-1 metrics; 2b/2c need their
eval contracts pinned first) → Phase 3 (anytime after Phase 1; fully
independent) → Phase 4 (needs Phase-0 semantics + Phase-1 harness).

Phase 3 may run in parallel with Phase 2. Nothing in Phase 2–4 may start
touching serving code before its proof/contract gate is recorded.

## Open questions for maintainer review

1. Watchdog cadence and alert channel (log-only vs. UI badge from day one)?
2. CI gate: ephemeral Qdrant via docker or recorded fixtures (no live
   dependency)?
3. Rewriter scope: rules-only first, or local-LLM fallback included from
   the start?
4. Unpark order: metrics slices 1–2 before anything else — confirmed?
