# Tasks: Plant RAG System

## Review Workload Forecast

Decision needed before apply: Yes
Chained PRs recommended: No
Chain strategy: size-exception
400-line budget risk: High

Estimated changed lines: 4,000–5,500 (~30 files): over the 400-line default and 2000-line session budget; single PR needs maintainer `size:exception` before apply. Paths relative to `src/plant_rag/`.

### Work Units

| Unit | Focused test command | Runtime harness | Rollback boundary |
|---|---|---|---|
| 1 Foundation | `pytest tests/test_smoke.py` | `docker compose up` | delete skeleton+compose |
| 2 Ingestion+reindex | `pytest tests/test_ingest.py` | `python scripts/ingest.py` | remove `ingest/` |
| 3 Retrieval+fast path | `pytest tests/test_retrieve.py` | `python scripts/query.py` | remove `retrieve/` |
| 4 Answering+queue | `pytest tests/test_answer.py tests/test_scheduler.py` | Ollama + `scripts/eval.py` | remove `answer/`, `scheduling/` |
| 5 Web+auth+E2E | `pytest tests/test_web.py tests/test_integration.py` | `streamlit run web/app.py` | remove `web/`, `users.json` |

## Phase 1: Foundation

- [x] 1.1 `git init`; `.gitignore` (venv, caches, qdrant data, users.json); commit docs.
- [x] 1.2 `requirements.txt`: ADR-002 pins + pytest; `pyproject.toml` src layout.
- [x] 1.3 `config.py`, `models.py`: endpoints, batch size, frozen `Hit`, `REFUSAL_ES`.
- [x] 1.4 `docker-compose.yml`: Qdrant v1.12 on_disk LAN port; check `/readyz`; smoke `pytest`.

## Phase 2: Ingestion

- [x] 2.1 `ingest/loader.py`: pymupdf extraction, 100-page batches, RSS bound.
- [x] 2.2 `ingest/chunker.py`: parent-child chunks: manual_id, page, parent, revision.
- [x] 2.3 `ingest/alarms.py`: `find_tables()` first cell `^\d{4,6}$`; line-regex fallback.
- [x] 2.4 `ingest/reindex.py`: build `r{N+1}`, validate, swap alias, delete `r{N}`; failure keeps `r{N}`.
- [x] 2.5 Tests: batches, provenance, replacement current, interrupted reindex never mixes.

## Phase 3: Retrieval

- [x] 3.1 `retrieve/embedder.py`: BGE-M3 fp32 CPU batched encoding.
- [x] 3.2 `retrieve/store.py`: `current_*` alias, revision filter, pages in `Hit`.
- [x] 3.3 `retrieve/fastpath.py`: code query → exact `MatchValue` on `current_alarms`; unknown empty.
- [x] 3.4 Tests: ES→EN pages, out-of-corpus empty, known/unknown alarm, no stale revision.

## Phase 4: Answering

- [x] 4.1 `answer/prompt.py`: Spanish prompt, closed safety terms, warning.
- [x] 4.2 `answer/llm.py`: Ollama (`num_ctx=8192`, `temperature=0.2`, `num_predict≤600`); empty → `REFUSAL_ES`, no LLM.
- [x] 4.3 `answer/citations.py`: validate `[Fuente: … Página N]` vs retrieved pages; violation → refusal.
- [x] 4.4 Tests: refusal gate, citation validator, safety warning, grounded answer.

## Phase 5: Query Queue

- [x] 5.1 `scheduling/scheduler.py`: heap `(aged, safety_rank, seq)`; aged ≥15s; safety 0; FIFO.
- [x] 5.2 Deadline `enqueued_at+45s` gates dispatch+generation → Spanish timeout; position helper.
- [x] 5.3 Tests: FIFO, safety preemption order, aging beats fresh safety, timeout never partial.

## Phase 6: Web+Auth

- [x] 6.1 `web/auth.py`: Argon2 `users.json`, owner flag, Spanish login error.
- [x] 6.2 `web/app.py`, `web/ui_es.py`: login gate, Spanish chrome, English excerpts.
- [x] 6.3 Owner ops: `.pdf` upload, UUID rename, reindex trigger, Spanish status, codes.
- [x] 6.4 Tests: valid/invalid login, owner gate, ES-chrome/EN split.

## Phase 7: Verification

- [x] 7.1 `scripts/{ingest,reindex,query,run_web}.py`; README: self-operation, LAN firewall, rollback.
- [x] 7.2 `scripts/eval.py`: scripted checks (alarm, ES→EN, refusal) pass (A4).
- [x] 7.3 Integration: Qdrant no mixed revision; batch RSS < 16GB.
- [x] 7.4 E2E: 45s timeout, safety preemption, login mobile/tablet; record (A1/A4).

## Phase 8: Verify Remediation (C1, C2, C3, W2)

- [x] 8.1 C1: bootstrap `src/` in all five entrypoints, fix `run_web` repo root
  and venv-aware Streamlit resolution, fix the README verification command;
  `--help` exit-code smoke tests (`tests/test_scripts.py`).
- [x] 8.2 C2: WA-R1 covering checks — bind-half assertions plus the README
  allow-LAN-then-drop firewall rule as release check (`tests/test_lan_access.py`).
- [x] 8.3 C3: make `web/app.py` actually runnable (undefined UI names, login
  label, missing `embed_question`, `_owner_code_remove` signature, `__main__`
  dispatch) and cover WA-R3 — upload starts ingestion with Spanish progress,
  reindex handoff, owner gating, login gate (`tests/test_owner_ops.py`).
- [x] 8.4 W2: `scripts/query.py` refusal sites share `REFUSAL_ES` with
  `plant_rag.models`, pinned by both CLI refusal paths.
