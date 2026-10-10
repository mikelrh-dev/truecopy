# Proposal: Post-audit hardening, round 2 (auth, floor, cache, exposure)

## Intent

A full audit of the shipped system (code, tests, specs, live services) surfaced
eleven issues between what the product claims and what it does: five medium and
six low. Two of them (the login lockout and the manual cache-invalidation
contract) were reproduced empirically before any change; one (the pool-level
floor) turned out to matter far more than its severity suggested, and the live
probe in `2026-10-10-zero-vector-index-guard` showed exactly why.

This change fixes all eleven, re-syncs the living specs, and adds the
regressions that were missing. It changes serving behavior in exactly two
places — the retrieval pool gate and the answer-cache key — and both are
fail-closed.

## Findings

| # | Severity | Finding | Fix |
|---|---|---|---|
| M1 | Medium | **Password with surrounding spaces locked the account out permanently.** `add_user` hashed the raw string; `authenticate` validated the trimmed one. | Verify the exact secret; trim only for the emptiness check. Regression test. |
| M2 | Medium | **The calibration sweep measured a path serving never walked.** It embedded the un-expanded question and probed dense-only `store.search`, so it could recommend a floor the hybrid path never applied. | Sweep embeds the ES→EN expansion and probes `retrieve_pool` (hybrid). Test pins the serving call. |
| M3 | Medium | **The text branch could admit passages the calibrated floor had rejected.** RRF ranks carry no cosine score, so a token-only match could reach the prompt. | Pool-level gate: one dense probe above `MIN_SCORE`; an empty pool feeds the exact refusal. Two tests + retrieval spec requirement. |
| M4 | Medium | **Qdrant was published on all interfaces with no authentication** — any LAN host could read or drop the whole corpus, bypassing the app login. | Bind `127.0.0.1` for REST and gRPC; documented in README/TUTORIAL. |
| M5 | Medium | **Answer-cache invalidation was a manual instruction** ("wipe the file"), so a stale answer could survive a floor/prompt/glossary change. | Config fingerprint in the cache key and record; a config change invalidates by construction. The calibration advisory now says so instead of asking for a wipe. |
| L1 | Low | Dead code and F821-class noise: `web/app.py::_do_reindex` opened a Qdrant connection and did nothing, unused locals/imports in `scripts/eval.py`, placeholders in f-strings. | Removed. `ruff`: 51 → 32, and the remaining 32 are the intentional `E402` (`sys.path` bootstrap) in `scripts/`. |
| L2 | Low | A fixed bootstrap owner credential (`admin-early-boot-owner`) shipped in the source. | Random per install (`secrets.token_urlsafe(16)`), printed once to the server console; second boot never resets an existing owner. |
| L3 | Low | `pyproject.toml` declares `dependencies = []`, so `pip install -e .` silently installs nothing. | Documented in the README: runtime deps live in `requirements.txt` (ADR-002). |
| L4 | Low | First query egresses to Hugging Face to fetch BGE-M3 + the reranker. | Documented as a one-time, first-run download; everything after it is offline. |
| L5 | Low | `web/app.py` cleared the whole `_ANSWERS` map at 1000 entries, which could drop another session's in-flight answer; the poll loop was a coarse 0.5 s. | Evict the oldest entries instead of clearing; poll at 0.25 s. |
| L6 | Low | Stale `__pycache__` from Python 3.10 visible locally. | Not actionable (gitignored). Stated as such rather than "fixed". |

## Scope

### In Scope

- `web/auth.py`, `web/app.py`, `src/plant_rag/retrieve/hybrid.py`,
  `src/plant_rag/answer/cache.py`, `src/plant_rag/calibration.py`,
  `scripts/eval.py`, `docker-compose.yml`.
- Tests: `tests/test_web.py`, `test_retrieve.py`, `test_answer_cache.py`,
  `test_calibration.py`, `test_scripts.py`, `test_integration.py`,
  `test_answer.py` (+7 net).
- Specs re-synced: `retrieval`, `answer-cache`, `min-score-calibration`,
  `web-access-auth`.
- Docs re-synced: README, TUTORIAL-PUESTA-EN-MARCHA, ARQUITECTURA, ROADMAP
  (test count, loopback note, bootstrap credential, dependency and egress
  notes).

### Out of Scope

- NO change to the answer contract: cited answer or exact refusal, unchanged.
- NO threshold change: `config.MIN_SCORE` default stays 0.40; the sweep still
  reports and does not apply.
- NO `noqa`/suppression to silence lint: the remaining `E402` are the
  entrypoints' `sys.path` bootstrap, reported as-is.
- NO rewrite of historical cache lines or calibration records.

## Decisions

| Decision | Rationale |
|---|---|
| Pool-level gate implemented as a dense probe (`limit=1`) rather than a client-side score filter | `Fusion.RRF` returns ranks, not branch scores, so "was this dense-backed?" is not answerable from the fused response. One extra local call is cheaper than admitting unvetted text-only evidence. |
| Cache invalidation by fingerprint instead of documenting the wipe | The wipe was a human step that could be skipped silently; a key that carries the config makes the stale-hit case impossible. The wipe instruction is kept as harmless. |
| Qdrant to loopback rather than adding authentication | Qdrant ships without auth; binding to loopback removes the exposure without inventing a secret to manage. Operators reach Streamlit, never Qdrant. |
| Random bootstrap password instead of a documented constant | A shipped constant is a public credential until rotated; a one-time random secret printed to the console has no window where a guess is valid. |

## Risks

| Risk | Mitigation |
|---|---|
| The pool gate turns degraded-but-usable retrieval into refusal | That is the intended fail-closed behavior (ADR-004). It is precisely what exposed the dead index: an empty pool is loud, text-only leakage is silent. See `2026-10-10-zero-vector-index-guard`. |
| Fingerprint changes the cache key shape | Old lines carry no `fingerprint` field and are skipped (a clean miss), never mis-served. Cache is disposable operator data (assumption A3). |
| Loopback binding breaks a remote Qdrant consumer | None exists: CLIs and the app run on the same host; the LAN rule in ADR-006 is about Streamlit. |

## Rollback

Revert the seven source files and the spec/doc deltas. No data migration is
involved: cache lines written under the new key are simply misses for the old
code, and the docker binding is one line per port.
