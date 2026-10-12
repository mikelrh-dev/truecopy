# Design / Execution Plan: RAG next level

Change: `rag-next-level`. Date: 2026-10-11. Scope of the *executable* part, the
order it lands in, the loop each batch runs, and the decisions this cycle
resolves before any code is touched.

## Objective

Turn the audit in `proposal.md` into landed, verified increments without
touching the refusal contract's meaning. Each batch is independently revertible
(code + tests + spec delta together) and each one ends green.

## Execution slices (ordered by value / risk, sized for one session each)

| Slice | Batch | Contents | Difficulty | Lands alone |
|---|---|---|---|---|
| 1 | **A** | E1 docstring truth, E5 truncation signal, E6 semantics documented | XS | yes |
| 2 | **B** | E4+M6 rerank-fallback signal | S | yes |
| 3 | **D** | M4 per-phase telemetry (+ the E7 prompt-size measurement) | S | yes |
| 4 | **C** | E3 per-corpus fastpath gating | M | yes |
| 5 | **E′** | M5 chunk citations, **redesigned** (see below) | M | yes |
| 6 | **F** | M3 bounded conversational memory — design first, then code | L | yes |
| — | **G** | M7 per-corpus glossary | S + curation | own change |
| — | **H** | M1 recalibration on the real corpus | S, live | needs services |

Ordering rationale: A is cosmetic and unblocks nothing, so it goes first and
cheap; B and D add *observability* with zero behavior change, and D is the
instrument slice 5 and slice 6 will be measured with, so D must precede E′;
C is the only behavior change in the first four and it is refusal-adjacent, so
it lands after the cheap wins on a clean tree; E′ and F are larger and both
depend on D's numbers.

## Per-batch loop (the protocol every slice runs)

```
explore ──→ spec ──→ build ──→ test ──→ verify
```

| Phase | What it means here | Skill that governs it |
|---|---|---|
| **explore** | Re-read the exact code + the tests that already pin the behavior. Output: what is *actually* true, and whether the finding needs a behavior change at all | `debugging-and-error-recovery` (root cause before patch) |
| **spec** | Update the delta in `specs/` and the batch in `tasks.md` before code. If the spec already covers it, restate the acceptance criteria from it | `spec-driven-development` |
| **build** | Red → Green → Refactor: the failing test exists *before* the implementation, in the project's hermetic style (injected seams, no models) | `test-driven-development`, `incremental-implementation` |
| **test** | `pytest tests -q` must stay at the batch's baseline **plus** the new tests, exit 0; lint must not grow past 32 `E402` | `test-driven-development` |
| **verify** | Per-finding compliance table with the evidence (test name + command + observed output), recorded in `verify-report.md` and mirrored to engram | `code-review-and-quality` |

No phase is skipped because the change "looks small": D6 below is a finding
whose explore phase *concluded against changing code*, and that conclusion is a
deliverable too.

## Decisions resolved in this cycle

**D7 — how a "log/metric line" is emitted (E5, E4/M6).** `src/` has no logging
precedent at all. Decision: stdlib `logging` with a module-level
`logger = logging.getLogger(__name__)`, emitted as a `warning`. Rationale: zero
new dependencies, no signature changes, no behavior change, and `caplog` makes
it hermetically assertable. Both signals share the one mechanism.

**D6 — E6 `position` is not stale, it is the designed meaning.** A3's own
instruction is "check pinned tests first". Checked: `test_scheduler.py` names
the contract (`test_timeout_reports_each_querys_submit_position`), two more
tests assert `"posición 1"`, and `models.TIMEOUT_ES` is an operator-facing
contract string quoting `posición N`. The frozen value *is* the submit
position — "you were 3rd when you asked". Decision: **document it, change no
behavior**. Renumbering or dropping the number would be a contract edit
justified by a cosmetic reading.

**C1 — the E3 switch shape.** Corpus capability resolved at *query* time, not
from new configuration: an alarm table counts as present only when
`current_alarms` resolves **and** holds at least one row under the current
revision. Rationale (from the proposal, confirmed): a config knob can drift
from the index it describes, and it would need a second place to keep in sync
on every reindex. Cost is bounded to the query shapes that need it: the probe
runs **only** on the unknown-code branch, so a known code and every
non-code-shaped question pay nothing. Implementation uses a `scroll(limit=1)`
scoped to the revision rather than `count()`: the existing test doubles already
model `scroll`, and `count()` would need a new method in every double.

**M4 — schema shape, and the trap it hides.** `records()` builds records with
`QueryRecord(**{k: data[k] for k in _FIELDS})`, a strict projection. Adding
`phases` to `_FIELDS` would make **every historical line raise `KeyError`** and
be silently swallowed by the corrupt-line handler — the entire existing history
would vanish from `stats()` and the web metrics without a single error. So:
`_FIELDS` stays the required schema, a new `_OPTIONAL_FIELDS = ("phases",)` is
merged when present, and `QueryRecord.phases` defaults to `None`. "When timing
is disabled the record keeps its historical shape" is thereby enforced by
construction, not by discipline.

**M5 — redesigned (why the original is under-scoped).** Two findings from
explore: (1) `child_id` exists in the payload (`reindex.py:272`) but is **not**
mapped by `hit_from_payload`, so "the child already rides in `Hit`" is wrong
for anything a validator can check; (2) the child is a character window **over**
the parent, so the original "carry the winning child alongside the parent"
duplicates text already in the prompt and worsens exactly the E7 overflow it
was meant to help. Redesign: the marker carries a **locator**
(`[Fuente: … Página N §c<index>]`), validated against the retrieved hit's
`child_id` (plumb it through — additive, the payload already has it, **no
reindex**); the prompt labels the winning fragment's index and section title
instead of re-emitting its text; the page-level marker stays valid and stays
the contract minimum. Open risk to be *measured*, not assumed: the citation
parser is a strict `fullmatch`, so a chunk suffix adds a failure mode to a 7B
that already fumbles markers ~1 in 3. Slice 5 therefore measures the marker
failure rate on the same traffic as the token delta, and ships only if the
rate does not regress.

**M3 (F) — the rule that keeps it safe.** History rewrites the query only (N
turns, small, configured), never evidence: no history object reaches
`build_messages`, citations resolve against current-turn retrieval, the 45s
deadline applies per turn, the refusal contract is byte-unchanged. One
interaction the original tasks missed: the answer cache keys on the question
string, so the rewrite must happen **before** `cache.lookup`/enqueue or a
follow-up would key entries by the un-rewritten text. Design is written first
(F1), as the batch requires.

## Gates per batch

| Gate | Command | Rule |
|---|---|---|
| Suite | `.venv/Scripts/python.exe -m pytest tests -q` | exit 0; count = batch baseline + new tests |
| Lint | `ruff check src web scripts tests` | must not exceed the 32 `E402` baseline (ruff 0.15.17 is global, not in `.venv`; `pyproject.toml` has no ruff config) |
| Compile | `.venv/Scripts/python.exe -m compileall -q src web scripts tests` | exit 0 |

Live end-to-end (`scripts/eval.py`, Qdrant + Ollama) is **not runnable in this
session** and is reported as NOT RUN, never as a pass.

## Evidence and memory

- `verify-report.md` in this folder: per-finding compliance table with the
  observable evidence, following the archived cycles' front-matter convention.
- engram (project `truecopy`): `sdd/rag-next-level/proposal` and
  `/design` at plan time; one `sdd/rag-next-level/<batch>` memory per landed
  batch, and `sdd/rag-next-level/verify-report` at the end.

## Out of scope for these slices

M8 multi-manual (separate change, design already written), model swaps, VRAM
budget, new services, cloud egress, and any rewrite of the refusal contract.
Slices E′, F, G and H are planned here but land as their own increments.

## Rollback

Every slice reverts on its own: the code, its tests, and its spec delta travel
together, no data migration exists anywhere in this program, and no slice
changes an on-disk schema that shipped data would have to be migrated for.

## Risks

| Risk | Mitigation |
|---|---|
| E3 and M5 touch refusal-adjacent paths | Every behavior change ships with hermetic tests pinning the *unchanged* cases (unknown code with a table still refuses exactly; page marker still validates); the eval goldens are untouched |
| Logging is brand new in `src/` | One shared mechanism, two call sites, both assertable with `caplog`; no raise path, no return-value change |
| Batch D's schema change silently drops history | The optional-field merge above, plus a test that a legacy line without `phases` still parses |
| M5 raises citation violations on a fumbly model | Measured before shipping (marker failure rate + token delta); page-level fallback stays the contract minimum |
