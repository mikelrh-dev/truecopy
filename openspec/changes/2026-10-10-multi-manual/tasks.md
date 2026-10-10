# Tasks: Multi-manual (N manuals live at once)

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~900+ across 5 phases (code + tests + specs + UI) |
| 400-line budget risk | HIGH — must be chained |
| Chained PRs recommended | Yes (one per phase) |
| Suggested split | A1 → A2 → A3 → A4 → A5 → B, each its own SDD/verify cycle |

Decision needed before apply: Yes (Phase B slot policy)
Chained PRs recommended: Yes
Chain strategy: feature-branch per phase, each independently revertible
400-line budget risk: High

Status: **DESIGN ONLY.** No task below is implemented in this session. The
tree still serves exactly one manual; the phases are the plan a follow-up
session executes one at a time.

## Phase A1 — Per-manual topology (retrieve.store)

- [ ] A1.1 Naming helpers: `text_collection(manual_id, revision)`,
      `alarm_collection(manual_id, revision)`, per-manual aliases
      `current__{manual_id}__text|alarms`
- [ ] A1.2 `live_manuals(client)` enumerates manuals from the aliases
      (single source of truth); empty when none
- [ ] A1.3 Keep today's behavior when exactly one manual is live (no selector
      needed); `revision` becomes per-manual
- [ ] A1.4 Tests: naming round-trip, enumeration, one-manual compatibility

## Phase A2 — Ingest/replace one manual (ingest.reindex)

- [ ] A2.1 `run_reindex` builds/replaces ONE manual's revision; the swap moves
      only that manual's alias pair (ADR-001 per manual)
- [ ] A2.2 Replacing manual B leaves manual A serving untouched
- [ ] A2.3 Tests: blue/green per manual; failure of B's build leaves both A and
      B's previous revision serving

## Phase A3 — Cache key + scope (answer.cache, answer.pipeline)

- [ ] A3.1 Cache key gains `(manual_id, scope)`; fingerprint unchanged (D4)
- [ ] A3.2 Pipeline resolves the operator's scope once; a shared revision
      number never collides across manuals
- [ ] A3.3 Tests: same question against two manuals stores two entries; scope
      is part of the key

## Phase A4 — Eval per manual (scripts.eval)

- [ ] A4.1 Eval loops `GOLDEN_SETS` per live manual; a manual without a set
      still FAILS loudly
- [ ] A4.2 `--manual` selector for a single manual; default = all live
- [ ] A4.3 Tests: two manuals evaluated; missing goldens fail

## Phase A5 — Web selector (web.app)

- [ ] A5.1 Manual picker in the question page; the selection scopes retrieval
- [ ] A5.2 Cited excerpts/figures resolve within the selected manual
- [ ] A5.3 AppTest: selecting a manual scopes the answer

## Phase B — All-manuals mode

- [ ] B1 Decide + measure the slot policy (how many candidates per manual
      before rerank) — its own SDD change
- [ ] B2 Cross-manual merge with explicit, tested slot allocation
- [ ] B3 Eval golden assertions for the merged mode
