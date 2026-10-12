# Tasks: RAG next level

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | Batches A–D ~250 total (E1–E6+M4+M6 are small, well-tested areas); Batch E′ (M5) ~150 (redesigned: locator + `child_id` plumbing, see `design.md`); Batch F (M3) ~300 + design; Batch H (M1) is process + a live run; M7 is its own change |
| 400-line budget risk | Low per batch if landed separately; land one batch per PR |
| Chained PRs recommended | No (batches are independent; M7 excluded) |
| Suggested split | One PR per batch A–H; M7 gets its own change folder |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch

## Batch A — Safe fixes (E1, E5, E6) · Difficulty: XS

- [ ] A1. `query_points` count: **RESOLVED by explore (D4)** — no test pins a single call (the only `len(calls)` assertions are `test_pipeline.py:247/255` on the variant query and `test_ingest.py:750` on a reindex count). Fix is docstring-only: `retrieve/hybrid.py:5-9` must describe TWO calls (dense probe + fused fusion).
- [x] A2. `_MAX_ALARM_PAGES` truncation (`retrieve/fastpath.py:36,167`): emit one `logger.warning` when the loop exits with a live `offset` (guaranteed truncation, no extra query, and no false positive when a code has exactly 10 pages), per `design.md` D7. Hermetic test with >10 same-code pages asserts the signal via `caplog`.
- [x] A3. Frozen `position`: **RESOLVED by explore (D6)** — `tests/test_scheduler.py` pins the submit-position semantics by name and `models.TIMEOUT_ES` quotes `posición N`; the frozen value IS the designed meaning. No behavior change: document the semantics in `scheduling/scheduler.py`. The "refresh at dispatch" and "drop the number" options are withdrawn.
- [x] A4. Full suite green → **383 passed**, exit 0; lint 32, unchanged; compileall 0 + lint (`ruff check src web scripts tests`; ruff 0.15.17 is a **global** install, NOT in `.venv`, and `pyproject.toml` has **no** ruff config) → must not exceed the 32 `E402` baseline in `scripts/`.

## Batch B — E4+M6: rerank-fallback signal (telemetry seed) · Difficulty: S

- [x] B1. Record the fallback: when `rerank()` keeps the incoming order due to load/scoring failure (`retrieve/rerank.py:91-95,100-102`), emit one stdlib `logger.warning` (never raise, never change the returned order) — `design.md` D7 fix for E4/M6.
- [x] B2. Hermetic tests via `caplog`: injected failing scorer → fallback order kept AND exactly one signal recorded; passing scorer → no signal recorded.
- [x] B3. Full suite green → **387 passed**, exit 0; lint 32, unchanged.

## Batch C — E3: per-corpus fastpath gating · Difficulty: M

- [x] C1. **DECIDED (design.md, C1)**: corpus capability resolved at QUERY time — `current_alarms` resolves AND holds ≥1 row under the current revision. No new config knob (it would drift from the index). Probe via `scroll(limit=1)` scoped to the revision, run **only** on the unknown-code branch so a known code and every non-code-shaped question pay nothing. Record the decision in the spec delta's rationale.
- [x] C2. Implemented: `fastpath.has_alarm_table` + the gate in `_answer_question` falls through to semantic search when the corpus has no table; the unknown-code refusal is unchanged where a table exists.
- [x] C3. Hermetic tests: `"2024"`/`"404"` on an alarm-less corpus retrieve semantically; unknown code on an alarm corpus still refuses exactly; eval `expect_alarms=False` path still green.
- [x] C4. Full suite green → **394 passed**, exit 0; lint 32, unchanged.

## Batch D — M4: per-phase telemetry (+ E7 measurement) · Difficulty: S

- [x] D1. Added phase timings (embed / Qdrant / rerank / LLM seconds) to the answer record path (`monitoring.py` + `build_record` call sites); units seconds, additive fields only, zero-cost shape when disabled.
  - **TRAP (`design.md`, M4)**: `records()` projects strictly with `QueryRecord(**{k: data[k] for k in _FIELDS})`. Adding `phases` to `_FIELDS` makes every historical line raise `KeyError` and be swallowed as corrupt — the whole existing history would silently vanish from `stats()` and the web metrics. Keep `_FIELDS` as the required schema, add `_OPTIONAL_FIELDS = ("phases",)` merged when present, and default `QueryRecord.phases` to `None`. Test that a legacy line without the field still parses.
- [x] D2. Hermetic tests: 5 of them, including the legacy-line guard for the `_FIELDS` trap and the cache-hit shape (no `phases` key at all).
- [ ] D3. **NOT RUN — needs live traffic** (Qdrant down: `127.0.0.1:6333` closed). E7 measurement using the new fields: log prompt character counts per answer over real traffic; report whether 5×page-parents vs `num_ctx=8192` ever overflows and at which end Ollama truncates. NO prompt change in this batch whatever the result.
- [x] D4. Full suite green → **392 passed**, exit 0; lint 32, unchanged.

## Batch E′ — M5: chunk-level citations (redesigned) · Difficulty: M

Redesign rationale in `design.md` (M5): `child_id` is in the payload
(`reindex.py:272`) but NOT mapped by `hit_from_payload` nor present on `Hit`,
and the child is a character window OVER the parent — so the original "carry
the child alongside the parent" duplicates prompt tokens and worsens E7.

- [ ] M5-a. Plumb `child_id` from the payload into `Hit` (`models.py` + `store.hit_from_payload`); additive, the payload already carries it → **no reindex**.
- [ ] M5-b. Extend the marker to a LOCATOR (`[Fuente: … Página N §c<index>]`) validated against the retrieved hit's `child_id`, with page-level fallback: the validator accepts both and the page-level marker remains the contract minimum (`answer/citations.py:30-41,59-81`).
- [ ] M5-c. Prompt labels the winning fragment's index + section title instead of re-emitting its text (`answer/prompt.py:108-128`).
- [ ] M5-d. Hermetic tests: chunk marker validates against the retrieved set; page marker still validates; mixed answer validates; invented chunk fails closed; legacy line/marker unaffected.
- [ ] M5-e. Full suite green + lint. Measure token delta per answer **and** the citation-marker failure rate in the telemetry from Batch D; ship only if the failure rate does not regress.

## Batch F — M3: bounded conversational memory (largest, needs design care) · Difficulty: L

- [ ] F1. Write the interaction design FIRST (inside this batch, before code): history rewrites the query only (max N turns, N small and configured), NEVER contributes evidence — no history object reaches `build_messages`; citations resolve against current-turn retrieval only; the 45s deadline applies per turn; refusal contract byte-unchanged.
- [ ] F2. Session state in the web layer only (Streamlit session), scheduler untouched; CLI stays single-shot.
- [ ] F3. Hermetic tests: follow-up resolves against rewritten query; planted history content can never become a citation (adversarial test: history contains a fake page number, answer must not cite it); blank/short history behaves like today.
- [ ] F4. Full suite green + lint.

## Batch G — M7: per-corpus glossary process · Difficulty: S (+ ongoing curation per manual)

Needs its own **retrieval spec delta**: G1 changes observable semantics (a
malformed per-corpus file must fail loudly at ingest, never silently degrade
retrieval), and this is the only code-bearing batch whose contract change was
not yet in `specs/`.

- [ ] G0. Add the delta to `specs/retrieval/spec.md` (per-corpus glossary source + loud failure) BEFORE the loader change.
- [ ] G1. Move glossary terms to a per-corpus data file with the current automotive set as the default; loader keeps word-boundary + accent-insensitive + cap semantics (`retrieve/expand.py:29-152` behavior pinned by existing tests first).
- [ ] G2. Hermetic tests: default glossary byte-identical behavior; corpus file overrides/adds terms; malformed file fails loudly at ingest, never silently degrades retrieval.
- [ ] G3. Full suite green + lint.

## Batch H — M1: recalibrate `MIN_SCORE` on the real corpus · Difficulty: S (needs live services)

This is the program's first item by value and had **no batch** (explore D2).
It cannot run in a hermetic session: it needs Qdrant + Ollama.

- [ ] H1. Run the calibration sweep against the real manual (the sweep already measures the serving path via `retrieve_pool` → hybrid, `scripts/eval.py:789-796`).
- [ ] H2. Record the per-manual calibration record + advisory output as the evidence (the calibration spec already owns this artifact).
- [ ] H3. Decide the floor from the measurement: keep 0.40, or move it. Config-only change; remember the cache contract — a `MIN_SCORE` change without a revision bump requires a cache wipe (state it in the PR when it happens).
- [ ] H4. If the floor moves, re-run the eval goldens and the full suite; the goldens must stay unchanged.

## Batch I — E2 (deferred by measurement) · not scheduled

The pool-level dense probe stays the single admission rule for text-assisted
recall (`specs/retrieval/spec.md`). Revisit ONLY with sweep evidence showing
rescued in-corpus queries and zero new out-of-corpus admissions.


Scale: XS trivial (<30 min) · S one module + tests (hours) · M cross-module or contract touch (1–3 days) · L architectural (a week+) · XL separate project.

| Item | Difficulty | Why |
|---|---|---|
| E1, E5, E6 (Batch A) | XS | One docstring / one `logger.warning` / one documented decision (E6 changes no code — explore D6) |
| E4, M4, M6 (Batches B, D) | S | Additive, no behavior change, well-tested areas |
| E3 fastpath gating (Batch C) | M | Touches the refusal-adjacent contract; needs adversarial tests |
| E7 prompt-size check (in Batch D) | S | Instrument only; decide after measuring |
| M1 recalibration on real corpus (Batch H) | S, live | Mostly process (run sweep, record, decide); code only if the floor moves; needs Qdrant + Ollama, so NOT runnable hermetically |
| M5 chunk citations (Batch E′) | M | `child_id` plumbing + locator marker + validator + prompt, backward compatible; ships only behind the measured marker-failure rate |
| M3 conversation (Batch F) | L | Session state, scheduler interplay, adversarial history test — design before code |
| M7 glossary per corpus (Batch G) | S + ongoing | Mechanical loader; the real work is curation per manual |
| M8 multi-manual | XL | Separate change; design exists at `openspec/changes/2026-10-10-multi-manual/proposal.md` |

## Explicitly NOT in these batches (separate future change)

- M8 multi-manual routing (collections per manual, alias semantics, per-manual eval loop, UI selector). Seeds already in the codebase: `manual_id` in payloads, page-level citations carrying the id, per-manual goldens in the eval.
- Rewriting the refusal contract, model swaps, new services, cloud egress.
