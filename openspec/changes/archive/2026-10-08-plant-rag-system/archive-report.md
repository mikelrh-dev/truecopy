# Archive Report: plant-rag-system

**Change**: plant-rag-system
**Archived to**: `openspec/changes/archive/2026-10-08-plant-rag-system/`
**Archived on**: 2026-10-08
**Artifact store**: hybrid — this file + Engram topic `sdd/plant-rag-system/archive-report`
**Verdict at close**: PASS WITH WARNINGS — 0 CRITICAL
**Repo**: `main`; commits made by this phase (see Commits section); nothing pushed (no remote configured)

## Sources Consulted (Final-State Authority ranking)

1. **Native review authority** — `reviewGate` structurally ABSENT: receipt-driven review was OFF for this candidate (review authority off; no review was ever started). Per the Native Review Receipt Gate, archive proceeds under ordinary repository policy. The review topics (`review/{transaction,ledger,receipt,gate-context}`) were NOT read — they do not exist for this candidate.
2. **Persisted tasks artifact** — `openspec/changes/plant-rag-system/tasks.md` read at gate time: 32/32 `[x]`, 0 `- [ ]`.
3. **Orchestrator final-state facts** (launch prompt, 2026-10-08) — most recent account; outranks intermediate snapshots.
4. **Intermediate snapshots** — `verify-report` #2771 (written at `47b9245`) and `apply-progress` #2765 (batch 5). Valid history; never presented below as current state.

**Engram observations read (traceability)**:
- #2757 `skill-registry` (config)
- #2759 `sdd/plant-rag-system/proposal`
- #2760 `sdd/plant-rag-system/spec`
- #2761 `sdd/plant-rag-system/design`
- #2763 `sdd/plant-rag-system/tasks`
- #2765 `sdd/plant-rag-system/apply-progress`
- #2771 `sdd/plant-rag-system/verify-report`

## Gates

| Gate | Result | Evidence |
|---|---|---|
| Native Review Receipt Gate | PASS (absent) | Review authority off for this candidate; kill-switch case → `reviewGate` absent → ordinary repository policy; nothing to read or block on |
| Task Completion Gate | PASS | Archived `tasks.md`: 32/32 `[x]`, 0 unchecked — no reconciliation needed |
| CRITICAL gate | PASS | 0 CRITICAL. Previous FAIL (C1 broken entrypoints, C2/C3 untested) fully remediated and re-proven; remediation settled in the native ledger with the failure binding satisfied (final-state facts); re-verification evidence below |

## Verification Final State (at close)

Per the orchestrator's final-state facts (rank 3), which supersede snapshot-era claims:

- Re-verification: **PASS WITH WARNINGS (0 CRITICAL)**
  - `pytest tests` via `.venv` → **190 passed**
  - 5 script entrypoints `--help` → **exit 0**
  - `compileall` → **exit 0**
  - seam imports OK

At verification time (snapshot #2771, `47b9245`): verdict `pass`, 19/19 requirements, 31/31 scenarios covered, 26/31 fully compliant, 4 ⚠️ PARTIAL, 0 ❌ FAILING, 0 ❌ UNTESTED, build exit 0, all 6 ADRs followed. That report already records the remediated C1/C2/C3 disposition; the launch prompt confirms the remediation settled in the native ledger afterward. No code changes occurred between `47b9245` and archive start.

**Contradiction recorded (not resolved silently)**: the launch prompt (2026-10-08) states "HEAD 7599b40 covers secondary-harness U5/Phase-7". Repository evidence at archive execution showed HEAD = `47b9245` (the tip of the 6 remediation commits), with integration commit `7599b40` ("feat: integrate web+auth and scripts+eval from secondary harness") seven commits earlier. The prompt's characterization of what `7599b40` covers matches the repository; only the "HEAD" designation conflicts with `git log`. Material to no verdict; recorded for accuracy.

## Warnings at Close

Carried from verify-report #2771 (state at verification time, still current at close):

- **W1** — Design `File Changes` table says `src/plant_rag/web/*`; implementation and tasks Phase 6 use top-level `web/`. Design deviation, no spec break.
- **W3** — `openspec/config.yaml` `verify.test_command: "pytest tests"` resolves to system Python 3.10 outside the project venv (exit 2). Evidence came from the venv interpreter. Orchestrator should point the config at the venv.
- **W4** — Alarm-code regex `^\d{4,6}$` compiled independently in `ingest/alarms.py` and `retrieve/fastpath.py`; cross-check test exercises fastpath only (suggestion S3 would close it).
- **W5** — Four scenarios remain PARTIAL pending live release evidence (listed below).

New from re-verify (final-state facts):

- **W6** — The web reindex button is a CLI handoff by design: it points the owner at `scripts/reindex.py` with Spanish guidance (the app stores no path to the last upload). Owner re-ingestion is reachable by re-uploading a PDF or by running the CLI; spec wording "trigger reindex … from the UI" is satisfied only indirectly.
- **W7** — Owner upload staging defaults to the repo-root CWD: each upload leaves a full manual PDF as `{uuid4}.hex}.pdf` in the working tree, and `*.pdf` is not gitignored. Pre-existing behavior, now exercised by tests. Future fix, not this change.

Suggestions: **S1 resolved** (entrypoint/README coverage pinned by `tests/test_scripts.py`); **S2 rejected with rationale** (`pip install -e .` impossible on this venv — Python 3.10 vs `requires-python >= 3.11`; floor must not be lowered); **S3/S4 carried** (cross-check alarm regex both sides; document POSIX vs Windows venv path spellings in README).

## Pending Release Evidence (recorded, NOT this change's scope)

Requires a live plant environment/devices; known-gapped by design:

1. Streamlit live boot + login gate against live Qdrant/Ollama.
2. `scripts/eval.py` 3/3 live vs the ingested corpus.
3. Tablet/viewport login observation on a real LAN device.
4. Ingestion RSS < 16GB on a full-manual run.
5. Off-LAN request rejection observed against the operator-configured firewall (release check pinned by `tests/test_lan_access.py`).

## Archive Decisions

1. **`verify-report.md` committed** (`4f9fb6d docs(sdd): record plant-rag-system verify report`) before the move. It is the terminal verification evidence and a listed change artifact per the OpenSpec convention; leaving it untracked would put audit evidence outside version control. `apply-progress` #2765's note "verify-report.md remains untracked on purpose (orchestrator artifact)" is a rank-4 snapshot from apply time, superseded by the launch prompt's explicit delegation of its fate to this phase.
2. **`handoff/` PACKET-A/B relocated** into `openspec/changes/archive/2026-10-08-plant-rag-system/handoff/` — the secondary-harness coordination notes now travel with the change's audit trail instead of lingering as stale repo-root clutter. NOT deleted: the archive is an audit trail and these packets document how commits under `7599b40` were handed off.
3. **No native receipt review** for this candidate (review authority off). Delivery followed ordinary repository policy: `single-pr` with maintainer-approved `size:exception` (tasks forecast: 4,000–5,500 lines, High 400-line budget risk, review budget 2000). Nothing pushed — no remote configured.
4. **`rules.archive` "Warn before merging destructive deltas" not triggered**: `openspec/specs/` contained only `.gitkeep`, so no main spec existed for any domain. All 5 deltas are full specs (no ADDED/MODIFIED/REMOVED/RENAMED sections); each was mechanically copied; zero existing requirements were touched or removed.

## Specs Synced (Step 2 — main specs created)

| Domain | Action | Details |
|--------|--------|---------|
| manual-ingestion | Created | 3 requirements / 6 scenarios |
| retrieval | Created | 3 requirements / 5 scenarios |
| answering | Created | 5 requirements / 8 scenarios |
| query-queue | Created | 4 requirements / 6 scenarios |
| web-access-auth | Created | 4 requirements / 6 scenarios |

Totals: 19 requirements, 31 scenarios — matches the verify-report envelope counts.

## Mechanical Copy Evidence (verbatim `diff -r` output in the phase result)

- **Step 2**: for each of the 5 domains, `diff -r` source delta vs temp copy → **empty** (5/5); post-copy readback `diff -r` source vs final `openspec/specs/{domain}/spec.md` → **all OK** (5/5 empty).
- **Step 3**: pre-move recursive snapshot vs `openspec/changes/archive/2026-10-08-plant-rag-system` → **empty**; source path confirmed absent before comparison.
- This `archive-report.md` is additive (created after the move) and excluded from the source/destination comparison by design.

## Step 4 Checklist

- [x] Main specs updated correctly (5 created, `diff -r`-verified)
- [x] Change folder moved to archive
- [x] Archive contains all artifacts: proposal.md, specs/ (5 domains), design.md, tasks.md, verify-report.md, handoff/ (PACKET-A/B)
- [x] Archived `tasks.md` has no unchecked implementation tasks (32/32)
- [x] Active changes directory no longer has this change (`Test-Path` → False)
- [x] Verbatim `diff -r` readback output included in the phase result and empty

## Commits Created by This Phase

| Commit | Message |
|---|---|
| `4f9fb6d` | `docs(sdd): record plant-rag-system verify report` |
| `1318aea` | `chore(sdd): archive plant-rag-system and promote specs` |
| (this file) | `docs(sdd): add plant-rag-system archive report` |

## SDD Cycle Complete

The change has been fully planned (proposal, 5 specs, design with 6 ADRs, 32 tasks), implemented (190 tests green), verified (PASS WITH WARNINGS, 0 CRITICAL; previous FAIL remediated and settled), and archived. Main specs are now the source of truth at `openspec/specs/{domain}/spec.md`.
