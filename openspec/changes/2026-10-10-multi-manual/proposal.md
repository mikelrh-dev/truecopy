# Proposal: Multi-manual (N manuals live at once)

Status: **DESIGN (proposal).** Implementation is deliberately NOT started in
the same session: this change rewrites the corpus-identity model (aliases,
revisions, cache key, eval loop, UI), so its first slice must land behind its
own gates. Nothing here is applied; the tree still serves one manual.

## Intent

The real use case is several documents (machine, quality, safety); today the
architecture serves exactly one current corpus by design (ADR-001 blue/green:
build `r{N+1}`, validate, swap `current_text`/`current_alarms` atomically,
delete the old). This change makes N manuals live at once, keeps replacement
per-manual (replacing one leaves the others serving), and lets an operator ask
against a selected manual or across all of them.

`manual_id` is already carried everywhere (payloads, citations, `GOLDEN_SETS`,
calibration records), so the identity exists; what is missing is the
**topology** that lets several of them coexist.

## Decisions resolved (the questions the roadmap left open)

### D1 — One collection per (manual, revision); per-manual aliases

Collections become `manual__{manual_id}__r{N}__text` / `...__alarms` (the
double underscore marks the manual boundary; `manual_id` is already sanitized
to `[0-9A-Za-z_]` by one shared helper). Each manual owns its own blue/green
alias pair:

- `current__{manual_id}__text` -> `manual__{manual_id}__r{N}__text`
- `current__{manual_id}__alarms` -> `manual__{manual_id}__r{N}__alarms`

Why not one shared collection with a `manual_id` filter: the atomic swap is
per-manual by requirement, and a shared collection would make one manual's
reindex touch the others' storage. Per-manual collections keep ADR-001's
"interrupted build leaves the old serving" property **per manual**, and Qdrant
aliases double as the live-manual registry (enumerate aliases by prefix — no
second source of truth).

Rejected: one collection per manual-revision only, no per-manual aliases
(the app would need its own registry file → two sources of truth, exactly the
drift blue/green exists to avoid).

### D2 — Revisions are per manual

`r{N}` counts within a manual, not globally. The global `revision_tag` alias
disappears; provenance is the pair `(manual_id, revision)`. Calibration records
already key on `manual_id` + `revision`, so they stay valid unchanged.

### D3 — Retrieval is scoped, with an explicit "all manuals" mode

A query runs against exactly one manual by default (the operator's selection);
an "all manuals" mode queries every live manual's alias and merges the pools
before rerank. Scope is a **filter on the alias set**, never a silent union:
merging by default would let a quality-manual page outrank the machine answer
and break the "one cited page per step" intuition.

### D4 — The answer cache key gains `(manual_id, scope)`; the fingerprint stays

Today the key is `(revision, model, config-fingerprint, question)`. With N
manuals a shared revision number collides, so the key becomes
`(manual_id, scope, revision, model, config-fingerprint, question)`. The
existing config fingerprint already invalidates on floor/prompt/glossary
changes and is unchanged.

### D5 — Ordering: one manual first, then "all"

Phase A ships the selector + per-manual scope with N manuals coexisting; the
cross-manual "all" mode is Phase B, because it needs merge/rerank semantics
(how many slots per manual) that are a product decision, not a wiring detail.

## Phased plan (each phase its own verify gate)

| Phase | Deliverable | Gate |
|---|---|---|
| A1 | `retrieve.store`: per-manual collection/alias naming + `live_manuals()`; keep single-manual behavior when only one exists | existing 381 tests green; new naming tests |
| A2 | Ingest/reindex: build/replace ONE manual's revision without touching others | blue/green per-manual tests; replacing manual B leaves A serving |
| A3 | Cache key + payload `manual_id`; pipeline resolves scope | cache tests; a shared revision never collides |
| A4 | Eval loops `GOLDEN_SETS` per live manual; `--manual` selector | eval per manual; a manual without goldens still fails loudly |
| A5 | Web selector (manual picker) + per-manual excerpts/figures | AppTest: selecting a manual scopes its answers |
| B | "All manuals" mode with slot allocation | measured merge quality; a new SDD decision on slot policy |

## Scope

### In Scope (this proposal)

The decisions D1–D5 and the phase plan; spec deltas for `manual-ingestion`,
`retrieval`, `answering`, `min-score-calibration`, and the web interaction.

### Out of Scope (explicitly not in this session)

- The Phase A1–B code itself (next session, one phase per SDD/verify cycle).
- Cross-manual de-duplication of near-identical pages.
- Per-operator manual permissions (the login has one operator role today).

## Risks

| Risk | Mitigation |
|---|---|
| Alias explosion / enumeration drift | Aliases ARE the registry; `live_manuals()` reads them, one source of truth |
| A shared revision colliding in the cache | D4 keys on `manual_id` + scope, tested |
| Cross-manual queries diluting answers | Phase B gated on a measured slot policy; Phase A is single-manual scope only |
| Eval silently skipping manuals | A live manual with no `GOLDEN_SETS` entry still FAILS loudly (existing rule) |
| Breaking the working single-manual path | Phases A1–A2 keep today's behavior when exactly one manual is live |

## Rollback

Each phase reverts independently (it is additive: new naming + a selector).
Until Phase A5, nothing changes for a one-manual deployment. `FIGURES_DIR` and
the cache already carry `manual_id`/`revision`, so figures and answers compose
without a second change.
