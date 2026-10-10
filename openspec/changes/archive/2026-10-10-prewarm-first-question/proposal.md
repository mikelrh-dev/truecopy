# Proposal: Warm the models before the first operator question

## Intent

The end-to-end budget is 45s (ADR-005). A cold console spent most of it loading
checkpoints: measured live on 2026-10-10, the first operator question took
**31.0s** with Ollama unloaded and a fresh process, versus **5-7.3s** for
questions once the process is warm. Nothing was broken; the first operator of
the day just paid the whole stack's startup inside their answer.

This change loads the three heavy models when the console opens — while the
operator is still logging in — and asks Ollama to keep the generation model
resident for a configurable window.

## Measured evidence (2026-10-10, Ollama unloaded before each run)

| Run | Pre-warm | First question |
|---|---|---|
| Fresh process, no warm-up | — | **31.0 s** |
| Fresh process, warm-up on open | 17.5 s | **18.4 s** |

Breakdown of the remaining 18.4s, measured per stage in the same process:

| Stage | Cost |
|---|---|
| Retrieval + CPU cross-encoder rerank | 8.2 s |
| First LLM call (prompt prefill) | 9.7 s |
| Second LLM call, same context | 0.9 s |

So the warm-up removes the checkpoint loads (~12.6s), and what is left is real
work: the CPU cross-encoder scoring the candidate pool, and the first prompt
prefill. The "<5s" target from the ROADMAP stays out of reach without touching
the reranker model itself (quantization / smaller model / ADR change), which is
explicitly NOT part of this change.

## Scope

### In Scope

- `src/plant_rag/answer/llm.py`: `warmup()` — one `num_predict: 1` request to
  `config.OLLAMA_URL`, response discarded, best-effort (never raises), carrying
  `config.OLLAMA_KEEP_ALIVE` when set.
- `src/plant_rag/retrieve/rerank.py`: `warmup()` — scores one throwaway pair
  through the same scorer seam the serving path uses. It calls the scorer
  directly instead of `rerank()`, because `rerank` degrades silently to the
  incoming order on failure and would report a false success.
- `src/plant_rag/config.py`: `OLLAMA_KEEP_ALIVE` (default `30m`) and
  `PREWARM_MODELS` (default on), plus the shared `_env_bool` helper.
- `web/app.py`: `prewarm_models()` (implementation, testable) + a
  `st.cache_resource` wrapper so Streamlit's per-interaction reruns pay it once;
  called from `main()` with a Spanish spinner label from `web/ui_es.py`.
- `tests/conftest.py`: `PLANT_RAG_PREWARM_MODELS=0` for the suite.
- Tests: 4 for the LLM warm-up, 3 for the reranker one, 3 for the app's.
- Spec: `answering` gains "No model load inside the answer budget".

### Out of Scope

- NO change to the answer contract, retrieval, thresholds or prompts.
- NO change to the reranker model, its size, or the number of candidates
  (that is the measured bottleneck and a separate decision with its own ADR).
- NO per-query `keep_alive`: the window is asked for by the warm-up only, so
  per-question payloads stay byte-identical to before.

## Decision: disable pre-warm under test instead of patching around it

The first version of this change called the models at app startup
unconditionally, and `tests/test_owner_ops.py` (which drives the real
`web/app.py` through Streamlit's `AppTest`) started loading BGE-M3 for real —
visible as a suite that jumped from ~16s to ~36s and one broken assertion
(`test_get_embedder_is_the_process_wide_instance`: the singleton must not be
loaded by the accessor). Hermeticity is a suite-level contract, so the fix is an
explicit switch the suite sets, not a fragile runtime sniff of "am I under
test". The suite now pins `PLANT_RAG_PREWARM_MODELS=0` in `conftest.py` before
anything imports `config`.

## Risks

| Risk | Mitigation |
|---|---|
| App startup now blocks ~17.5s before the login gate renders | The cached call carries a Spanish spinner label, so the operator sees progress instead of a blank page; the cost is paid once per process |
| Holding ~4.7GB of VRAM between questions | It is the stated trade-off of `OLLAMA_KEEP_ALIVE` (default `30m`, settable to `""`) |
| Warm-up failing (Ollama down) delays the app | `warmup()` never raises; failures surface as `False` in the readiness map and the app serves |
| Loading models for a visitor who never logs in | Accepted: the console is LAN-only and the app exists to be asked questions |

## Rollback

Set `PLANT_RAG_PREWARM_MODELS=0` (instant, no code change) or revert the four
source files. The warm-up writes nothing: no corpus, log, cache or calibration
is touched.
