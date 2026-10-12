# Exploration: RAG next level

Change: `rag-next-level` — audit fixes (E1–E7) + recall/understanding program
(M1–M8). Date: 2026-10-11. Second, independent pass over the audit behind
`proposal.md`, plus the gate inventory the execution loop will rely on.

## Re-verification of the audit (every claim re-checked against code)

| ID | Claim | Independent check | Verdict |
|---|---|---|---|
| E1 | Docstring says ONE `query_points` call | `hybrid.py` module docstring vs the `dense_probe` call + the fused call = **two** | Confirmed |
| E2 | Pool-level `dense_probe` empties the pool when dense is below floor | `hybrid_search` returns `()` on an empty probe regardless of the text branch | Confirmed |
| E3 | A bare 4–6-digit query is a guaranteed refusal without an alarm table | `lookup` → `_resolve` → `()` when `current_alarms` is absent → `refusal()` in `_answer_question` | Confirmed (but see D1) |
| E4 | Reranker failure degrades silently, no record | `rerank.py` returns `tuple(hits)[:top_k]` on load/scoring failure with no signal anywhere | Confirmed |
| E5 | `_MAX_ALARM_PAGES=10` truncates silently | `_resolve` exits on `len(hits) >= _MAX_ALARM_PAGES` even with a live `offset` | Confirmed |
| E6 | `position` is frozen at submit | Computed in `submit`, stored on `_Entry`, quoted by three `timeout_answer` sites | Confirmed (see D6) |
| E7 | 5 page-sized parents vs `num_ctx=8192` | `build_messages` emits `hit.parent` for up to `TOP_K`; `GENERATION_OPTIONS["num_ctx"] = 8192` | Confirmed as an open measurement |
| M4 | Only total latency is recorded today | `build_record` carries `latency_s` and no phase field | Confirmed |
| M5 | "the child already rides in `Hit`" | `Hit.child` exists — but `child_id` does **not** (see D5) | Partially confirmed |

The three "refuted suspicions" of the proposal also hold: revision **is** in the
log (`_revision_tag`), the calibration sweep **does** measure `retrieve_pool`,
and the web **does** catch `LLMError`.

## Gate inventory (what can actually be run here)

| Gate | Command | Baseline | Notes |
|---|---|---|---|
| Suite | `.venv/Scripts/python.exe -m pytest tests -q` | **381 passed**, exit 0 | `pipefail` + `PIPESTATUS` checked, not inferred from the summary |
| Lint | `ruff check src web scripts tests` | **32 errors, all `E402` in `scripts/`** | `ruff 0.15.17` lives in the global `Python310/Scripts`, **not** in `.venv`; there is **no `[tool.ruff]`** section in `pyproject.toml`, so the tasks' "per `pyproject.toml`" is inaccurate |
| Compile | `.venv/Scripts/python.exe -m compileall -q src web scripts tests` | exit 0 | Cheap import sanity |
| Live end-to-end | `scripts/eval.py` | **NOT AVAILABLE** | Needs Qdrant + Ollama; this session is hermetic only. The live calibration item (M1) cannot be closed here |

## Defects found in the plan itself (all fixed before execution)

| # | Defect | Resolution |
|---|---|---|
| D1 | E3's "live example" (`COMO-DECLINE-EL-SISTEMA.md:19`, `"¿quién fue el presidente de españa en 1995?"`) is **not** code-shaped: that document itself classifies it as "camino semántico". Only a bare `"1995"` would be refused by shape | The finding stands; the cited example is replaced by the bare-code shape in `proposal.md` |
| D2 | **M1 had no batch** in `tasks.md` even though it is the program's first item by value, while M7 did (Batch G) | Added **Batch H (M1)** with the process steps and the recalibration decision rule |
| D3 | Item labels `E1–E7` mean *errors* in `proposal.md` but *Batch E items* are also numbered `E1–E4` — a real hazard in a document executed batch by batch | Batch E items renumbered to `M5-a…M5-d` |
| D4 | A1 says "first grep tests for any pinned single-`query_points` assertion" | **No test pins a single call** (the only `len(calls)` assertions are about the variant query in `test_pipeline.py:247/255` and a reindex count in `test_ingest.py:750`) → E1 is a docstring-only fix |
| D5 | M5 says the child "already rides in `Hit`", implying zero plumbing | `child_id` **is** in the payload (`reindex.py:272`) but is **not** mapped by `hit_from_payload` nor present on `Hit`. Worse, the child is a character window **over** the parent, so re-emitting it in the prompt duplicates tokens already there and worsens E7 | M5 is redesigned in `design.md` (locator, not transcript) and demoted out of the first execution slice |
| D6 | A3 says "check pinned tests first, then refresh at dispatch or drop the number" | `test_timeout_reports_each_querys_submit_position` pins **submit-position semantics by name**, plus three more tests and the `TIMEOUT_ES` contract string quote `posición N`. The staleness is the designed meaning, not a bug | A3 becomes: document the semantics in `scheduler.py`; **no behavior change** |
| D7 | The project has **no logging precedent** in `src/`, yet E5 and E4/M6 both ask for a "log line" | Decided in `design.md`: stdlib `logging`, module-level logger, `caplog` in tests. No new dependency, no behavior change |

## Environment notes

- Windows + Git Bash. Project venv at `.venv/Scripts/python.exe` (Python 3.11);
  the suite's `__pycache__` shows a `cpython-310` pass too, so both interpreters
  have run it.
- `engram` v1.16.1 is installed and this repository's memories live under
  project **`truecopy`** (the git remote name — the project *is* `plant-rag`,
  the remote is not). Every SDD cycle in `openspec/changes/archive/` has a
  matching `sdd/<cycle>/...` memory there; this cycle follows the same keys.
- No `openspec` CLI is installed (`npx --no-install openspec` fails); the
  workflow is the folder convention documented in `openspec/config.yaml`.
