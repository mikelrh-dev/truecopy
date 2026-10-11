---
title: Verify Report — prewarm-first-question
type: architecture
created: 2026-10-10
updated: 2026-10-10
change: prewarm-first-question
verdict: pass
---

# Verify Report: Warm the models before the first operator question

**Change**: prewarm-first-question (first question no longer pays the model load)
**Branch**: `docs/roadmap` (working tree; not yet committed)
**Verified on**: 2026-10-10

## Verdict

**PASS** on the hermetic suite and on a live, controlled cold measurement. The
change removes the checkpoint loads from the first question; it does not, and
does not claim to, reach the ROADMAP's "<5s" target — the measurement says why.

## Gates

| Gate | Result | Evidence |
|---|---|---|
| Full suite | PASS | `.venv/Scripts/python.exe -m pytest tests -q` → **373 passed**, exit 0 (was 363; +10 tests) |
| Hermeticity | PASS | Suite runs in ~16 s with no checkpoint loaded and no request to Ollama; `tests/conftest.py` pins `PLANT_RAG_PREWARM_MODELS=0` |
| Lint | PASS (baseline) | `ruff check src web scripts tests` → 32 findings, all `E402` in `scripts/` (unchanged) |
| Compile | PASS | `compileall -q src web scripts tests` → exit 0 |
| Knob validation | PASS | `0` → `False`, `1` → `True`, `quizas` → `ValueError: PLANT_RAG_PREWARM_MODELS must be a boolean, got 'quizas'` |
| Live cold measurement | PASS | Ollama unloaded, fresh process: **31.0 s** first question without warm-up vs **18.4 s** with it (warm-up 17.5 s at app open) |

## Per-requirement compliance

| Requirement (answering spec) | Evidence | Verdict |
|---|---|---|
| No model load inside the answer budget | `web/app.py::prewarm_models` + cached `_prewarm_models` called from `main()`; measurement above | PASS |
| Warm-up is best-effort, never an outage | `llm.warmup`/`rerank.warmup` swallow failures and return `False`; `tests/test_answer.py` (failure), `tests/test_retrieve.py` (failing scorer), `tests/test_web.py` (broken embedder) | PASS |
| Disableable for hermetic contexts | `PREWARM_MODELS` knob + `conftest.py`; `tests/test_web.py::test_prewarm_is_disabled_in_the_hermetic_suite` | PASS |
| Warm-up writes nothing | No corpus, log, cache, or calibration call in either helper; the reranker probe and the Ollama ping are read-only | PASS |

## Honest limits

- The "<5s" target is **not met and not addressed here**. The measured remainder
  is retrieval + CPU cross-encoder rerank (8.2 s) and first-call prefill (9.7 s,
  of which a repeated call costs 0.9 s). Cutting it means changing the reranker
  (quantization, a smaller cross-encoder, or fewer candidates) — a separate
  decision with an ADR, deliberately out of this change.
- The warm-up keeps ~4.7 GB of VRAM resident for `OLLAMA_KEEP_ALIVE` (default
  `30m`). That is a deliberate trade-off, not an oversight; `""` disables it.
- "App startup" means when the Streamlit script first runs for a session, not
  when the process boots: the operator waits ~17.5 s behind a Spanish spinner
  before the login gate renders. Moving that work before the first browser
  connection would need a startup hook outside Streamlit's model.
- The measurement is one question on one machine; the ratio (not the absolute
  seconds) is the transferable part.
