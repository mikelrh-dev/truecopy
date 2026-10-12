# Proposal: RAG next level — audit fixes + recall/understanding program

## Intent

Verified audit of the serving RAG (2026-10-11, code-read, suite run: **381
passed**), turned into a fix program and an ordered improvement program for a
later agent to execute via SDD. Nothing here is inferred from memory: every
claim cites `file:line` checked this session.

A **second, independent pass** (`explore.md`) re-checked every claim, fixed
four defects in this document itself (D1–D4) and resolved four open decisions
(`design.md`: D7 logging, D6 E6 semantics, C1 switch shape, M4 schema trap).
Read `explore.md` and `design.md` before executing any batch.

## Verified state (do not re-derive)

Single answering chain (`answer/pipeline.py`, gates at 309-314 + `retrieve_and_rank`
137-170) with deterministic citation contract (`answer/citations.py:59-81`),
exact refusal, one bounded retry (`answer/llm.py:267-309`), 45s deadline with
safety-preempting scheduler (`scheduling/scheduler.py:63-67`, aging at :43),
hybrid dense+text RRF (`retrieve/hybrid.py:105-150`), CPU reranker with
graceful degradation (`retrieve/rerank.py:91-102`), exact alarm fastpath
(`retrieve/fastpath.py:128-167`), retrieval-only ES→EN glossary
(`retrieve/expand.py:9-19`, capped at :152), revision+model answer cache,
feedback + decline-strong-pool signals (`monitoring.py:172-251`), per-manual
calibration records with advisory, warmup/prewarm, Streamlit LAN console with
Argon2id auth.

Refuted suspicions (verified false — do not re-investigate):
- The log DOES record corpus revision (`pipeline.py:244-250,282` via
  `_revision_tag`).
- The calibration sweep DOES measure the serving path (`retrieve_pool` →
  hybrid at `scripts/eval.py:789-796`, not `store.search`).
- The web DOES catch `LLMError` around answering (`web/app.py:474-479`).

Already decided elsewhere (do not relitigate):
- Directed retry: measured 0/3 conversion, rejected with data (`ROADMAP.md`).
  Pool-wrong root cause, not retry shape.
- Multi-manual: design resolved, not implemented
  (`openspec/changes/2026-10-10-multi-manual/proposal.md`).

## Findings: errors (plan in `tasks.md`, fix direction here)

| ID | Severity | Location | Finding | Fix direction |
|---|---|---|---|---|
| E1 | Low (doc) | `retrieve/hybrid.py:5-9` vs `:129-140` | Docstring claims ONE `query_points` call; the `dense_probe` is a second one | One-line comment fix; first grep tests for any pinned single-call assertion |
| E2 | Medium (design constraint) | `retrieve/hybrid.py:14-21,124-140` | The text branch can never rescue on its own: pool-level `dense_probe` empties the pool when dense is below floor even on a perfect token match. Documented as intent ("recall AID, never independent evidence"), but it caps recall and contradicts the naive reading of the ES→EN expansion purpose (`expand.py:13-16`) | Record the invariant in spec; relax ONLY behind measurement during M1 (recalibration), never by default |
| E3 | Medium | `retrieve/fastpath.py:131-135` + `answer/pipeline.py:311-312` | Any exact 4–6-digit query (`"2024"`, `"404"`, `"1350"`) is a guaranteed refusal on a corpus with no alarm table — no semantic fallback, and no runtime switch (per-manual `expect_alarms=False` exists only in the eval at `scripts/eval.py:283-297`). The bare form is what refuses: `COMO-DECLINE-EL-SISTEMA.md:19` is NOT an instance of this — that document's own worked example ("¿quién fue el presidente de españa en 1995?") is classified there as the semantic path, because only a bare digit string is code-shaped | Per-corpus fastpath gating (see retrieval delta); unknown-code refusal preserved where a table exists |
| E4 | Low | `retrieve/rerank.py:91-95,100-102` | Reranker failure degrades silently to retrieval order with no record anywhere — an observability blind spot in a system that measures everything else | Record the fallback (log/metric), no behavior change |
| E5 | Very low | `retrieve/fastpath.py:36,167` | `_MAX_ALARM_PAGES=10` truncates silently when one code spans more pages | Emit the truncation (log at ingest or query count) instead of silent cut |
| E6 | Cosmetic | `scheduling/scheduler.py:127,151,161,169` | `position` is frozen at submit; the timeout message can quote a stale position | Refresh at dispatch or drop the number from the message (check pinned tests first) |
| E7 | Measure first | `answer/prompt.py:108-128` + `answer/llm.py:45-49` | Five full page-sized parents against `num_ctx=8192`: dense pages may overflow the window and Ollama would truncate silently at an unverified end. NOT asserted — log prompt sizes first, then decide | Telemetry task inside M4; no prompt change until measured |

## Program (ordered by value/risk; scope per item in tasks)

| # | Item | Why now | Risk |
|---|---|---|---|
| M1 | Recalibrate `MIN_SCORE` on the real manual (+ per-manual) | 0.40 comes from a 3-page corpus (`config.py:134-141`); sweep + records + advisory already exist | Low–Medium |
| M2→E3 fix | Per-corpus fastpath gating (remedy for E3) | Highest value on alarm-less enterprise corpora | Low–Medium |
| M3 | Bounded conversational memory | The step from answering to understanding: history rewrites the query only (max N turns), NEVER contributes evidence; citations resolve against current-turn retrieval; deadline applies per turn; refusal contract byte-unchanged | Medium |
| M4 | Per-phase telemetry in answer records | Only total latency exists today; phase timings (embed/Qdrant/rerank/LLM) are what tune timeouts and catch degradations (incl. E4, E7-measurement) | Low |
| M5 | Chunk-level citations | Page-only cites are imprecise on long pages. **Redesigned in `design.md`**: `child_id` is in the payload but NOT in `Hit`, and the child is a window OVER the parent, so re-emitting it duplicates prompt tokens and worsens E7. Marker carries a locator, validated against `child_id`, page-level fallback kept | Medium (token cost + citation-violation risk, measured before shipping) |
| M6 | Rerank-fallback signal | Remedy for E4 (one log/metric line) | Low |
| M7 | Per-corpus ES→EN glossary | Current glossary is automotive and static (`expand.py:26-28` says extending is a product decision); enterprise manuals need their own | Low (process) |
| M8 | Multi-manual routing | `manual_id` already flows through payloads, citations and goldens — but exactly ONE manual is current (`current_*` aliases). Collections-per-manual + routing + per-manual eval. **Separate future change, not in these tasks** — seeds only | High |

M8 from the earlier analysis (embedding batching) is folded: `retrieve_and_rank` already skips the second embed when the pool is full (`pipeline.py:156`), so no change without measurement — M4 telemetry will say where latency actually hurts.

## Risks

| Risk | Mitigation |
|---|---|
| Changing refusal-adjacent behavior (E3, M3) weakens the never-invent contract | Every behavior change ships with hermetic tests pinning refusal on the same cases the eval pins; eval goldens unchanged |
| Calibration churn (M1) invalidates cached answers | Config change without revision bump already requires cache wipe per contract — the task states it explicitly |
| Conversation history contaminating evidence (M3) | History is query-rewrite input only, enforced by construction (no history object reaches the prompt builder) |

## Rollback

Each batch reverts independently (code + tests + spec delta for that batch only). No data migration anywhere in this program.

## Out of scope

- M8 multi-manual implementation (separate change; seeds in tasks).
- Model swaps, VRAM budget changes (ADR-002), new services, cloud egress.
- Rewriting the refusal contract itself.
