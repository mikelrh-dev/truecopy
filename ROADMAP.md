# ROADMAP — pending work

Status date: 2026-10-10 · hermetic suite @ 362 green on this branch · horizon: the three
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

**What is missing:** ONE bounded, DIRECTED retry when the model declines with a
full pool and budget to spare (reuse `CITATION_RETRY_MIN_BUDGET_S`).

**Hard constraint — the shape of the retry:** it must NAME the target
passage ("read this excerpt; if it answers the question, answer from it"),
because generic pressure caused a measured incident: nudging a model that had
declined made it answer an out-of-corpus question (cooking pasta) from an
unrelated page (the horn page) — see `answer/llm.py:208-214`. A directed retry
can only ground on the named page, and the citation validator still guards it.

**Evidence required before implementing:** event frequency from the
`--signals` view plus operator confirmation. If declines-with-full-pool are
rare or the pool is wrong, this stays unimplemented — detection costs nothing,
a bad retry costs trust.

**Touch points:** `answer/llm.py` (both decline paths, mirroring the existing
one bounded retry), `config.py` (budget knob), tests in `test_answer.py`.
Because it changes answering behavior: full SDD cycle.

**Done when:** conversion rate measured, contract unchanged (a violating retry
collapses to the exact refusal), hermetic suite green, change archived.

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
