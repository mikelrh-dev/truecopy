# Tasks: Warm the models before the first operator question

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~230 (code ~70, tests ~90, spec ~30, docs ~40) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, one work unit (warm-up + knob + tests) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: Low

Open question resolved: warm-up is a config-gated behaviour (`PLANT_RAG_PREWARM_MODELS`,
default on) rather than a runtime "am I under test?" detection, because the
suite drives the real app through AppTest and hermeticity is a suite contract.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | The first question of the day never pays the model load | `.venv/Scripts/python.exe -m pytest tests/test_answer.py tests/test_retrieve.py tests/test_web.py -q` | Revert the two warm-up helpers + the app hook + the knob |

## Phase 1: Package-level warm-up

- [x] 1.1 RED: `tests/test_answer.py` — `llm.warmup` sends one `num_predict: 1` chat request, carries `keep_alive` only when configured, and returns `False` (never raises) when the transport fails
- [x] 1.2 GREEN: `llm.warmup()` with payload `{model, messages, stream: False, options: {num_predict: 1}}`
- [x] 1.3 RED: `tests/test_retrieve.py` — `rerank.warmup` scores exactly one throwaway pair, reports `False` on a failing scorer and on a disabled reranker
- [x] 1.4 GREEN: `rerank.warmup()` calling the scorer directly (calling `rerank()` would degrade silently and report a false success)

## Phase 2: Config and the app hook

- [x] 2.1 `config._env_bool` + `PREWARM_MODELS` (default true) + `OLLAMA_KEEP_ALIVE` (default `30m`), with the rationale comment
- [x] 2.2 `web/app.py`: `prewarm_models()` (implementation) + `st.cache_resource` wrapper called from `main()`, spinner text from `web/ui_es.py::PREWARM_LABEL`
- [x] 2.3 RED: `tests/test_web.py` — disabled ⇒ `{}` and nothing loaded; enabled ⇒ the three loads in order; a failing embedder ⇒ `False` without raising

## Phase 3: Hermeticity repair

- [x] 3.1 Reproduce the regression: `tests/test_owner_ops.py` (AppTest over the real app) made the suite load BGE-M3 → `test_get_embedder_is_the_process_wide_instance` failed and the run went ~16s → ~36s
- [x] 3.2 `tests/conftest.py` pins `PLANT_RAG_PREWARM_MODELS=0` before importing `config`
- [x] 3.3 Suite back to hermetic: 373 passed in ~16s

## Phase 4: Verification

- [x] 4.1 `.venv/Scripts/python.exe -m pytest tests -q` → **373 passed**, exit 0
- [x] 4.2 `ruff check src web scripts tests` → 32 findings, all `E402` in `scripts/` (unchanged baseline)
- [x] 4.3 `compileall` → exit 0
- [x] 4.4 Live measurement with Ollama unloaded: 31.0 s cold → 18.4 s warm, per-stage breakdown recorded
- [x] 4.5 `PLANT_RAG_PREWARM_MODELS` validated: `0`/`1` parse, `quizas` raises a named `ValueError`
