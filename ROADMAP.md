# ROADMAP — pending work

Status date: 2026-10-10 · hermetic suite @ 373 green on this branch · horizon: the three
items below. This file is the agent-facing index: what is left, what evidence a
next step needs, and where it plugs in. Deep rationale lives in the archived SDD
changes under `openspec/changes/archive/`.

## Project context (why the constraints are what they are)

- Local-only RAG for plant operators: Spanish questions over machine/business
  manuals; nothing leaves the machine (no cloud, no egress).
- The answer contract is the product: cited `Manual + Page` or the exact
  refusal `No aparece en la documentación`. The model is never the source of
  truth — every citation is validated against the retrieved set
  (`answer/citations.py`), and empty evidence never reaches the LLM.
- Corpus is **enterprise manuals** (procedures, quality, safety, machine), not
  only alarm tables: the alarm fast path is optional per manual
  (`expect_alarms=False`), and the ES→EN glossary + `MIN_SCORE` are calibrated
  **per corpus** — a manual in Spanish makes the expansion unnecessary.
- Every behavior change goes through the SDD cycle (openspec + Engram).

## 1. Directed retry on `decline-strong-pool` (roadmap item 4, phase 2)

**Shipped (phase 1):** the pipeline records `{"event": "decline-strong-pool"}`
into `logs/queries.jsonl` whenever the model declines while the pool was full
(`len(hits) >= config.TOP_K`), carrying pool size, pages, manual and revision.
Read it with `scripts/feedback_report.py --signals`. The signal follows the
`log=` seam, so eval traffic never emits one (`tests/test_pipeline.py`).

**Evaluated 2026-10-10 and REJECTED (phase 2 does not ship).** A directed
retry was implemented (one bounded call that NAMES the top-ranked passage),
covered hermetically, and trialled live; the measurement says no.

- **Frequency:** 4 of 22 representative in-corpus questions declined next to a
  full pool (~18%), so the event is not rare — the phase-1 gate was met.
- **Conversion:** the retry fired 3 times and recovered **0** answers
  (`outcome="declined"` ×3).
- **Root cause is upstream, not the retry.** Spot-checks against the text
  index: `donde esta la rueda de repuesto` was pointed at page 210 — which
  DOES contain "spare tire" — and the model still declined (the model, not the
  naming, is the bottleneck). `como cambio la rueda` was pointed at the
  reranker's top-1 (page 73) while the answer lives on 106/210/207 — the
  "pool is wrong" case. `como activo el limpiaparabrisas` had wiper pages
  (217/218/327) in its pool yet took the citation-fumble path, which the
  directed retry does not cover.

The roadmap's own gate anticipated this: *"If declines-with-full-pool are
rare or the pool is wrong, this stays unimplemented — detection costs nothing,
a bad retry costs trust."* The pool is wrong for exactly these queries.

**Decision:** the retry code and tests were reverted; phase 1 detection stays.
The real follow-up is **retrieval quality for wheel/jack/wiper-class
questions** (why the text-branch pages 210/106/207 do not survive rerank into
the top-1), not a second LLM call. That would be its own SDD change, measured
against the same probe (`outputs/rag-probe/signal_probe.py`).

**Evidence artifacts (gitignored, this machine):**
`outputs/rag-probe/signal-probe*.out`, `outputs/eval-r9*.log`.

**Done when:** conversion rate measured (done — 0/3, negative), contract
unchanged (the reverted tree keeps it).

## 2. Live eval 3/3 (carried as W3)

**Shipped:** `scripts/eval.py` runs four checks against a live stack (known/
unknown alarm code, ES→EN cross-lingual, out-of-corpus refusal, per-manual
goldens) plus the `MIN_SCORE` sweep, and can record a calibration line
(`--record`). The hermetic suite (362 tests) proves the contracts with doubles.

**What is missing:** the live run itself. Qdrant has been down on the dev
machine, so the wiring (real embedding → real Qdrant fusion → rerank → Ollama →
validator) has not been exercised end to end in recent cycles.

**Action:** `docker compose up -d`, ingest a manual (20–25 min on CPU), run
`scripts/eval.py`, record latencies (target <5 s, contractual limit 45 s) and
the four check results in the release notes. Operational, no code change; if
the sweep recommends a different floor, applying `PLANT_RAG_MIN_SCORE` is an
owner decision (eval informs, owner applies).

**Done when:** 4/4 checks pass on a real manual and the sweep shows in-corpus
above / out-of-corpus below the current floor.

## 3. Multi-manual (roadmap item 5)

**Design resolved 2026-10-10 (design-only, not implemented):** the open
questions are answered in `openspec/changes/2026-10-10-multi-manual/proposal.md`
— per-manual collections `manual__{id}__r{N}__{text,alarms}` with per-manual
aliases (the aliases ARE the live-manual registry), per-manual revisions, an
explicit scoped retrieval ("all manuals" is a separate Phase B with its own
slot-policy decision), and a cache key that gains `(manual_id, scope)`. The
phased plan is A1 topology → A2 per-manual reindex → A3 cache → A4 eval →
A5 UI → B cross-manual merge. Each phase is its own SDD/verify cycle.

**Shipped:** exactly one current corpus by design (ADR-001 blue/green: build
`r{N+1}`, validate counts, swap `current_text`/`current_alarms` atomically,
delete the old). `manual_id` is already carried in payloads, citations,
golden sets (`GOLDEN_SETS` keyed by `manual_id`) and calibration records.

**What is missing:** N manuals live at once. Open design questions a proposal
must answer: one collection per manual vs per manual-revision; what the
`current_*` aliases mean with N corpora; retrieval scoped to the selectedmanual vs across all; the answer cache key must gain `manual_id` (today it is
  revision+model+config-fingerprint+question and a shared revision number would
  collide); the eval
loops per manual; a selector in the web UI.

**Why last:** highest value (the real use case is several documents — quality,
safety, machine) but highest risk: it touches the alias design, the cache key,
the eval loop, monitoring and the UI. A project, not a change: full SDD cycle
with explicit design decisions.

**Done when:** an operator asks a question that only a second manual answers,
gets the cited answer, and switching/replacing one manual leaves the others
serving.

## Housekeeping (optional)

Operator credentials: the local `users.json` / `.streamlit-users.json` never
reached the public repository (public history starts clean); they exist only in
the local pre-publication history. Rotating them is optional hygiene, done by
the owner from the web UI.
