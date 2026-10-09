# Handoff Packet A — Web + Auth (Phase 6, tasks 6.1–6.4)

Scope: implement ONLY these files. Do NOT modify anything under `src/plant_rag/` except nothing — core is read-only for you. Read-only usage of core APIs is expected.

## Files to create

- `web/__init__.py` — package marker
- `web/auth.py` — Argon2 user store (`users.json`), owner flag, Spanish login errors
- `web/app.py` — Streamlit app: login gate, question box, queue position display, answer rendering
- `web/ui_es.py` — Spanish UI strings (chrome ES, manual excerpts stay English)
- `tests/test_web.py` — valid/invalid login, owner gate, ES-chrome/EN split
- `tests/test_integration.py` — end-to-end query through scheduler + retrieval + fake LLM transport

## Required reading (in this repo, BEFORE coding)

- `openspec/changes/plant-rag-system/specs/web-access-auth/spec.md` — normative requirements
- `openspec/changes/plant-rag-system/design.md` Decision 6 (ADR-006) — LAN bind, Argon2, owner flag
- `src/plant_rag/models.py` — `Answer` (kinds: `answer`/`refusal`/`timeout`), `REFUSAL_ES`, `TIMEOUT_ES`
- `src/plant_rag/scheduling/scheduler.py` — `Scheduler(generate, clock)`, `submit() -> int`, `run_once() -> Answer | None`, `queue_position_es()`
- `src/plant_rag/answer/llm.py` — `generate(question, hits, remaining_s, transport)`; raises `LLMError`, `DeadlineExceeded`
- `src/plant_rag/retrieve/fastpath.py` — `lookup(client, query)` → `None` (semantic) / `()` (refusal) / tuple (alarm hits)
- `src/plant_rag/retrieve/store.py` — `search(client, query_vector, top_k)` → `tuple[Hit, ...]`
- `src/plant_rag/config.py` — all tunables (`QUERY_DEADLINE_S=45`, `MIN_SCORE`, `OLLAMA_URL/MODEL`, `TOP_K`); operator text stays Spanish

## Call sequence for one operator question

1. `position = scheduler.submit(query)` → show `queue_position_es(position)`.
2. Worker loop calls `scheduler.run_once()`; `None` means idle.
3. The wired `generate(query, remaining_s)` function MUST do, in order: `fastpath.lookup()` → code-shaped-unknown `()` yields refusal `Answer(kind="refusal", text_es=REFUSAL_ES)` with NO LLM call; non-shaped `None` goes embed → `store.search` → `llm.generate(question, hits, remaining_s=remaining_s)`; non-empty tuple maps `AlarmHit` rows to an `Answer` with citations.
4. Catch `LLMError` in the web layer and show a Spanish infrastructure error (e.g. service unavailable, retry later). NEVER let it crash the UI. `DeadlineExceeded` is already converted to the Spanish timeout inside `run_once()` — display it as-is.

## Hard rules

- `users.json` (real hashes) is gitignored and MUST never be committed. A `users.json.example` shape may be committed if helpful.
- Uploads: `.pdf` only, UUID-renamed files, parsed in-process via `ingest/` modules.
- Bind Streamlit to the LAN interface; document the firewall rule in code comments (full README is Packet B).
- Answer contract is inviolable: every `answer` cites Manual + Page; unknown → exact `REFUSAL_ES`; safety topics carry the Spanish warning (use `answer.prompt.is_safety_query` + `SAFETY_WARNING_ES`, do not retype strings).
- Conventions: `pyproject.toml` src layout (`pythonpath=["src"]`), pytest files `tests/test_*.py`, conventional commits, English code/comments, Spanish UI strings only.

## Acceptance

- `pytest tests/test_web.py tests/test_integration.py` green; full `pytest tests/` stays green (currently 129 passed).
- `streamlit run web/app.py` boots, login gate blocks anonymous access, owner flag gates upload/reindex/codes.
- Rollback: delete `web/`, `tests/test_web.py`, `tests/test_integration.py`, `users.json` — core untouched.
