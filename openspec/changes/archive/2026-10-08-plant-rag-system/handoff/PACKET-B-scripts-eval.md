# Handoff Packet B — Scripts, Eval, README (Phase 7, tasks 7.1–7.4)

Scope: operator tooling + scripted evaluation + docs. Do NOT modify `src/plant_rag/` core or `web/`. Depends on Packet A landing first (only `run_web.py` needs the web app; everything else needs the core only).

## Files to create

- `scripts/ingest.py` — CLI: load a PDF (100-page batches) + build `r{N+1}` + validate + alias swap (thin wrapper over `ingest.loader/chunker/alarms/reindex`)
- `scripts/reindex.py` — CLI: replacement flow for a new manual revision (blue/green; failure keeps serving `r{N}`)
- `scripts/query.py` — CLI: one-shot question → retrieval → answer (for owner spot-checks; reuses the same `generate` wiring as the web layer)
- `scripts/run_web.py` — CLI: launch Streamlit bound to LAN (`streamlit run web/app.py --server.address <lan>`)
- `scripts/eval.py` — scripted eval set, MUST implement the three mandatory checks (assumption A4):
  1. alarm retrieval (known code → cited page; unknown code → exact refusal),
  2. cross-lingual ES→EN (Spanish query finds English passages),
  3. refusal (out-of-corpus → exact `REFUSAL_ES`, zero invention).
  Exit non-zero on any failure. Record per-check latency (A1: <5s target, 45s contractual).
- `README.md` — self-operation runbook: install, ingest, reindex, user management, LAN firewall rule, rollback plan, `size:exception` note (single-PR delivery).
- Integration evidence (task 7.3, report as run output, not necessarily committed files): Qdrant never serves mixed revisions; ingestion batch RSS stays under 16GB.
- E2E record (task 7.4): 45s timeout observed, safety preemption order observed, login from mobile/tablet browser observed.

## Required reading (in this repo, BEFORE coding)

- `openspec/changes/plant-rag-system/specs/manual-ingestion/spec.md` (batch/provenance/reindex scenarios) and `specs/answering/spec.md` (eval requirement A4)
- `openspec/changes/plant-rag-system/design.md` — ADR-001 (blue/green), ADR-002 (pins + memory math), Testing Strategy
- `src/plant_rag/config.py` — every tunable is env-overridable (`PLANT_RAG_*`); scripts MUST NOT hardcode ports, models, or thresholds
- `src/plant_rag/models.py` — `REFUSAL_ES` exact string for eval assertions
- `src/plant_rag/retrieve/store.py` — `MIN_SCORE = 0.40` is PROVISIONAL: `eval.py` MUST include a calibration step that sweeps the threshold against labeled ES→EN + out-of-corpus cases and reports the recommended value (do not silently change the default; report it)

## Hard rules

- Scripts are thin CLIs over `src/plant_rag` modules — no duplicated ingestion/retrieval logic.
- `eval.py` asserts byte-exact refusal text (`No aparece en la documentación`) and at least one `[Fuente: … Página N]` citation per grounded answer, validated against retrieved pages.
- Keep tests hermetic: `pytest` suite must pass without Qdrant/Ollama running; live checks stay in scripts/harness output.
- Conventions: src layout, `tests/test_*.py`, conventional commits, English code/comments, Spanish operator-facing strings only.

## Acceptance

- `python scripts/eval.py` passes 3/3 checks against a live stack (Qdrant + Ollama) and records latencies.
- `pytest tests/` stays green (currently 129 passed).
- README lets the owner (single operator, no backoffice) go from empty repo to serving LAN users.
- Rollback: delete `scripts/`, `README.md` — core and web untouched.
