---
title: Verify Report — rag-next-level
type: architecture
created: 2026-10-11
updated: 2026-10-11
change: rag-next-level
verdict: pass (hermetic); live gates run later same day, infra fix proven
---

# Verify Report: RAG next level

**Change**: `rag-next-level` — audit fixes (E1–E7) + recall/understanding
program (M1–M8)
**Scope landed and verified here**: slices 1–4 = **Batch A, B, D, C**
**Branch**: `main`, working tree only — nothing committed (not requested)
**Verified on**: 2026-10-11

## Verdict

**PASS** on the hermetic gates for the four landed batches, with the test count
growing monotonically and the lint baseline untouched.

**LIVE GATES**: run later the same day after Docker Desktop was started. The
served corpus turned out to be **dead for an infrastructure reason unrelated to
this change** (bind-mount storage defect, fixed separately). The live run
exposed that failure, confirmed the landed code changes via records, and
triggered the storage fix. See "Live verification (added later the same day)"
before trusting any live number in this file.

**INFRA FIX (separate, authorized)**: the recurring dead-corpus defect was
proven to be the Windows bind mount and fixed with a named Docker volume.
Verified at both restart levels (container restart and Docker engine restart).
That fix lives in `docker-compose.yml` and the corpus rebuild was captured as
`manual_r12_text`.

## Gates

| Gate | Result | Command / evidence |
|---|---|---|
| Full suite | **PASS** | `.venv/Scripts/python.exe -m pytest tests -q` → **394 passed**, exit 0 (381 → 383 → 387 → 392 → 394, exit 0 checked with `pipefail`/`PIPESTATUS` at every checkpoint) |
| Lint | **PASS (no growth)** | `ruff check src web scripts tests` → **32 errors at every checkpoint**, all `E402` in `scripts/`; each touched file passes on its own (`ruff` 0.15.17, a **global** install — it is not in `.venv`) |
| Compile | **PASS** | `.venv/Scripts/python.exe -m compileall -q src web scripts tests` → exit 0 |
| Live end-to-end (first pass) | **NOT RUN** | needs Qdrant (closed) + Ollama (open) |
| Live end-to-end (later same day) | **RUN** | Docker Desktop up + Qdrant up; corpus was dead, fixed separately (see below) |
| M1 recalibration (Batch H) | **NOT RUN / LIVE ITEM** | same reason; Batch H is a live process, not a hermetic gate |

## Per-batch compliance

| Batch | Finding | Requirement (spec) | Evidence | Verdict |
|---|---|---|---|---|
| A | E1 | The module docstring must describe what the code does | `hybrid.py` docstring now documents the TWO calls (dense probe + fused) and why the probe cannot be folded into the fused call | PASS |
| A | E5 | Truncation is observable, never silent (`retrieval` delta) | `test_alarm_code_past_the_scan_cap_emits_one_truncation_signal` (12 pages → 10 rows + exactly 1 warning naming the code); `test_alarm_code_exactly_at_the_scan_cap_emits_no_signal` (10 pages → 0 warnings) | PASS |
| A | E6 | Timeout/queue contract unchanged | No code change; `test_timeout_reports_each_querys_submit_position` and the three `"posición 1"` assertions still pass **unmodified**; the snapshot semantics are now documented in `scheduler.py` | PASS (documented, not changed) |
| B | E4/M6 | Fallback recorded once, order preserved (`answering` delta) | `test_rerank_scoring_failure_keeps_order_and_signals_once`, `test_rerank_unloadable_model_keeps_order_and_signals_once`, plus the two no-signal guards (healthy scorer, disabled by configuration) | PASS |
| D | M4 | Phase timings additive, historical shape preserved (`answering` delta) | `test_phase_timings_round_trip_through_the_log`; `test_a_record_without_timings_keeps_its_historical_shape` (key ABSENT, not null); `test_a_line_written_before_phase_timings_still_parses` (the `_FIELDS` trap guard); `test_a_semantic_answer_records_every_phase_it_ran` (four keys, non-negative, sum ≤ total + rounding); `test_a_cache_hit_records_no_phases_while_the_miss_records_them` | PASS |
| C | E3 | Per-corpus fastpath gating (`retrieval` delta) | `test_alarm_table_capability_needs_the_alias_and_a_current_row`; `test_digits_on_an_alarm_free_corpus_answer_semantically`; `test_unknown_alarm_code_returns_exact_refusal` (now with a REAL alarm table + answer-ready transport, so the exact refusal proves no semantic fallback ran) | PASS |
| D | E7 | Measure before changing the prompt | Instrument in place; the measurement itself is NOT RUN (needs live traffic) and **no prompt change was made**, as the batch requires | NOT RUN (by design) |

## Files changed

| File | Change |
|---|---|
| `src/plant_rag/retrieve/hybrid.py` | E1 docstring truth (two calls, and why) |
| `src/plant_rag/retrieve/fastpath.py` | E5 warning + module logger; E3 `has_alarm_table` capability probe |
| `src/plant_rag/retrieve/rerank.py` | E4/M6 fallback warning with its cause |
| `src/plant_rag/scheduling/scheduler.py` | E6 semantics documented (no behavior change) |
| `src/plant_rag/answer/pipeline.py` | M4 `_add_phase` wiring; E3 gate consults the capability probe |
| `src/plant_rag/monitoring.py` | M4 `phases` as the first OPTIONAL field + historical-shape JSON |
| `tests/test_retrieve.py` | +6 tests; the Qdrant double now pages faithfully |
| `tests/test_integration.py` | +1 test, 1 test strengthened; the alarm `scroll` double now honors its filter |
| `tests/test_monitoring.py` | +3 tests |
| `tests/test_pipeline.py` | +2 tests |
| `COMO-DECLINE-EL-SISTEMA.md` | Documents the E3 behavior change for the operator |
| `openspec/changes/2026-10-11-rag-next-level/*` | `explore.md`, `design.md`, spec deltas, `tasks.md` |

## Two double-fidelity fixes (they were hiding meaning)

1. `tests/test_retrieve.py`'s Qdrant double always answered `offset=None`, so a
   truncated alarm scan was **unobservable** — the E5 signal could not have
   been tested at all. It now pages like the server (a next offset only while
   rows remain).
2. `tests/test_integration.py`'s double ignored `scroll_filter` on alarm
   scrolls, so **an unknown code "resolved" to any row present**. That would
   have inverted the E3 tests. It now applies the same filter emulation as the
   other double, which also makes the pre-existing unknown-code test genuine.

Both fixes change only test doubles, never product code; no assertion was
weakened, and no test was skipped to make anything pass.

## Deliberately NOT done

- **No commits**: the user did not ask for them. Batches sit in the working
  tree, each independently revertible.
- **Batches E′ (M5), F (M3), G (M7), H (M1)**: planned, not landed in this
  session. E′ is redesigned in `design.md` (locator + `child_id`) and must ship
  behind a measured marker-failure rate; F needs its design first; G needs its
  own spec delta; H needs live services.
- **No prompt change** for E7: the spec forbids deciding before measuring.

## Live verification (added later the same day)

**Status change**: the live gates that this report first marked NOT RUN were
run afterwards, with Docker Desktop started and Qdrant up. Findings:

1. **The served corpus was dead, and that is why nothing answered.**
   `manual_r10_text` (1211 points, `current_text`) served **all-zero vectors**:
   cosine 0.0 against everything, below the 0.40 floor, so the pool was empty
   and *every* question got the exact refusal in 0.2–3.2 s with no LLM call.
   `manual_r8_text` was the same. The **text branch was alive** (`"indicator
   lights"` → pages 15, 16 — the golden pages — while the system refused), which
   is the live measurement E2 asked for: the pool-level dense probe is a single
   point of failure, not a mild recall cap.
2. **My changes verified live.** E3: the bare `"2024"` took the semantic path
   (`qdrant_s 0.25` + `embed_s 0.11` in the record) instead of the instant
   shape refusal. M4: the real log carries per-phase timings, and they show the
   **reranker is the bottleneck** (`rerank_s` 5.3–6.8 s) ahead of the LLM
   (2.9–10.1 s) and far ahead of Qdrant (0.05–0.25 s).
3. **Corpus repair (authorized): proven, then lost to the storage defect.**
   `outputs/rag-probe/rebuild_r11.py` re-embedded the 1211 stored `child_text`
   values into `manual_r11_text` (646 s), validated counts, confirmed a real
   dense score above the floor (0.685/0.657/0.649) and swapped the aliases.
   The system then answered, and the five questions scored: `presion
   neumaticos` **9/10** (golden p99; the cited page carries "42 psi (290 kPa)"
   verbatim), `Como configuro el limite de velocidad` **9/10** (golden p147),
   `que luces de testigo hay en el cuadro` **7/10** (golden p15, but the ABS
   claim is cited to p77, which does not contain "ABS" — live evidence for M5),
   `¿quién es el presidente de francia?` **10/10** (exact refusal, 0.3 s,
   `llm_s = 0.0`), `"2024"` **3/10** as an answer with correct behavior.
4. **Root cause of the recurring dead corpus (proven, not hypothesized).**
   `manual_r10` was ALIVE when built (`outputs/eval-r10.log`, 2026-10-10 23:12:
   4/4 PASS with real calibration scores) and dead the next day. The write path
   is healthy today, the zero-vector guard predates that ingest by 47 min, and
   the container logs report a clean `100%` recovery. Decisive experiment: two
   scratch collections (5 vectors each, `on_disk=True` and `on_disk=False`) both
   collapse to norm 0.0 after `docker compose stop && up` with points intact,
   while the SAME test on a **named volume** survives with identical scores.
   → the Windows **bind mount** (`./qdrant_data:/qdrant/storage`) does not
   persist vectors, and no ingest-time guard can ever see it.
5. **Current state, stated plainly**: the restart experiment re-zeroed `r11`
   too, so the corpus refuses everything again. Rebuilding before fixing the
   storage is ~11 minutes thrown away at the next restart.
6. **Storage defect FIXED and proven (authorized, infra only).**
   `docker-compose.yml` now uses the named volume `ragproyect_qdrant_data`
   instead of `./qdrant_data:/qdrant/storage`, with the measured reason written
   into the compose comment; the old folder stays as rollback evidence. Data
   migrated with `docker cp` (955 MB, all 6 collections, aliases intact),
   vectors rebuilt into `manual_r12_text` (1211 points, 637 s) and validated:
   dense search **0.685/0.657/0.649** against a 0.40 floor. **Acceptance test,
   at both restart levels: (a) `docker compose stop && up` and (b) a full
   Docker ENGINE shutdown + restart (the closest reproduction of the machine
   shutdown that killed r10) → the served revision keeps norms 1.0 and
   identical scores 0.685/0.657/0.649, and an end-to-end question answers
   (`presion neumaticos` → cited p99 with "42 psi (290 kPa)", 21.5 s cold: the
   first question after an engine restart pays the model loads).** The r10/r11
   revisions stay dead (their vectors were lost before the fix) and are
   unreachable through any alias.
7. **The verification loop found a reproducibility problem worth its own item.**
   The five questions were re-run after the fix: four reproduced exactly
   (`presion neumaticos` 9/10, `limite de velocidad` 9/10, out-of-corpus 10/10,
   `"2024"` 3/10), but `que luces de testigo hay en el cuadro` flipped from a
   grounded answer (7/10) to the exact refusal after 30.3 s — `llm_s 24.7` on a
   failed citation validation plus its bounded retry. Retrieval is stable; the
   7B's marker discipline is not, and the failure costs ~30 s (against a 45 s
   deadline that also has to cover queue time). That is measured support for
   the M5/retry work, and it means single-run scores on marker-fragile
   questions are not reproducible evidence.
8. **The repo had already documented the symptom.**
   `TUTORIAL-PUESTA-EN-MARCHA.md` described exactly this failure ("tras
   reiniciar Docker… los vectores en disco se pusieron a cero… el bind mount de
   Windows pierde los mmap; los payloads sobreviven"), with the vector-read
   triage snippet and "reindexa" as the remedy. The compose was never changed
   and the `zero-vector-index-guard` cycle instead blamed a stub encoder —
   contradicted by the evidence above. The tutorial is updated to the named
   volume, its backup recipe and the rebuild path.

## Limitations

- Everything above is **hermetic**. The suite exercises the real
  `answer_question` → `Scheduler` → `validate_citations` chain with Qdrant and
  Ollama doubled; the network path (real Qdrant scoring, real 7B marker
  behavior, real prompt sizes) is **not** covered by this evidence.
- The live scores in the section above are my judgement checked against the
  cited pages' real text, not a human expert's review; the full
  `scripts/eval.py` run was not repeated after the repair.
- The E3 change therefore remains **unverified against a real alarm-less
  enterprise manual**: the hermetic proof is structural (alias absent/empty →
  semantic), not a live measurement of recall gained. The proposal's own
  measurement plan (M1/H, plus the eval's `expect_alarms=False` path) is what
  closes that.
