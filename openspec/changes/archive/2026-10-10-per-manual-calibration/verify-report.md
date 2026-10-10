```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:343846f8626ea7432c78cfa87bb50cc766b72d396fe268e5f4ebb8cf2ed948c6
verdict: pass_with_warnings
blockers: 0
critical_findings: 0
requirements: 10/10
scenarios: 20/20
test_command: .venv/Scripts/python.exe -m pytest tests -q
test_exit_code: 0
test_output_hash: sha256:35d6c739707a1868481df2e17b4ce27e6ba961b6d8f894d87e43cab205cd038d
build_command: .venv/Scripts/python.exe -m compileall -q src web scripts tests
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

# Verify Report: per-manual-calibration

**Change**: per-manual-calibration
**Mode**: Standard (strict TDD inactive)
**Repo**: branch `feat/per-manual-calibration` — 5 commits on `origin/main` (b8c0f03, 1b6cbfd, c40573a, 63d0d8b, f8f4d1c); working tree clean at verification start
**Scope**: report-only — no source, test, spec, or task file modified; no PR, push, merge, or review lenses launched

### Completeness

Envelope basis: complete = scenario with a passing covering test at runtime (COMPLIANT + PARTIAL); FAILING/UNTESTED = 0. Fully-compliant subtotal 19/20.

- Tasks total 12 / complete 12 / incomplete 0 (`openspec/changes/per-manual-calibration/tasks.md` contains zero `- [ ]`; Engram tasks id 2819)
- Spec counts read from the delta specs: min-score-calibration 8 req / 12 scn; answering 1 req / 4 scn; manual-ingestion 1 req / 4 scn → **10 requirements, 20 scenarios** (matches Engram spec id 2817)

### Build & Tests Execution

- Build: `.venv/Scripts/python.exe -m compileall -q src web scripts tests` → **exit 0**, 0 output bytes (sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855)
- Tests: `.venv/Scripts/python.exe -m pytest tests -q` → **Run 1: 326 passed, 5 warnings, 21.46s, exit 0. Run 2 (digest run): 326 passed, 5 warnings, 21.34s, exit 0**. test_output_hash sha256:35d6c739707a1868481df2e17b4ce27e6ba961b6d8f894d87e43cab205cd038d over the exact captured stdout+stderr bytes of run 2. Warnings = 5 pre-existing SwigPy/SwigPyObject/swigvarlink DeprecationWarnings (also present before this change).
- Hermeticity: the suite requires no live Qdrant/Ollama (per suite design); expected count 326 matched exactly.

### Verify-Scope Isolation Evidence (commands + results)

| Check | Command / evidence | Result |
|---|---|---|
| `retrieve/` + `answer/` untouched | `git diff --name-only origin/main..HEAD -- src/plant_rag/retrieve src/plant_rag/answer` | empty (0 files) |
| Web upload path untouched | `git diff --name-only origin/main..HEAD -- web` | 0 files |
| `reindex.py` NOT wired | `rg -n "calibration" scripts/reindex.py` | no matches (exit 1) |
| Pinned eval cache seam intact | `rg -n "AnswerCache|cache=" scripts/eval.py` | no matches (exit 1) |
| No serving module mentions calibration | `rg -n "calibration" src/plant_rag/retrieve src/plant_rag/answer` | no matches (exit 1) |
| Serving precedence unchanged | `src/plant_rag/config.py:96` `MIN_SCORE = _env_float("MIN_SCORE", 0.40)`; retrieval reads `config.MIN_SCORE` only (`src/plant_rag/retrieve/hybrid.py:106`, `src/plant_rag/retrieve/store.py:239`); env validation pinned by `tests/test_answer.py:287-294` | env over config default, unchanged |
| eval.py additive-only | `git diff origin/main..HEAD -- scripts/eval.py`: docstring +34-38, import :66, optional `EvalResult.golden_pass/golden_total` default `None` :128-129, counts on passing golden result :627-628, `_best_separating()` extraction :807 (moved identical logic out of `report_calibration`, same branches and messages), `_append_calibration_record` :866, mutually-exclusive argparse group :926-940, append gated on `args.record and all_passed and report.points` :1005-1009. The report-only line `scripts/eval.py:861` `"The eval did NOT change config.MIN_SCORE."` is byte-identical to `origin/main:835` and unconditional inside the same returned report string | no check, printed line, or exit path changed |
| Config knob | `src/plant_rag/config.py:141` `CALIBRATION_PATH = _env_str("CALIBRATION_PATH", "logs/calibration.jsonl")` (empty string disables) | matches design |
| Diff shape | `git diff --stat origin/main..HEAD` | 14 files, +1764 / −12 (openspec docs included); 5 commits |

### Spec Compliance Matrix (10 req / 20 scenarios) — 19 COMPLIANT, 1 PARTIAL, 0 FAILING, 0 UNTESTED

| # | Requirement | Scenario | Covering test / evidence | Result |
|---|---|---|---|---|
| 1 | Append-only record schema v1 | Record round-trip | `tests/test_calibration.py:89` `test_record_round_trip_preserves_every_v1_field` (V1_KEYS set equality :96, `+00:00` :98) | ✅ COMPLIANT |
| 2 | Append-only record schema v1 | Append never rewrites | `tests/test_calibration.py:101` `test_append_never_rewrites_prior_lines` (byte-identical prefix :111) | ✅ COMPLIANT |
| 3 | Configurable store path | Default path | `tests/test_calibration.py:401` (default `logs/calibration.jsonl`) composed with `tests/test_scripts.py:279` (eval writes to whatever `config.CALIBRATION_PATH` is) | ✅ COMPLIANT |
| 4 | Configurable store path | Disabled store | `tests/test_calibration.py:198` (all seams no-op), `tests/test_scripts.py:329` (`--record` silent, exit 0, nothing written), `tests/test_ingest.py:667` (no advisory printed) | ✅ COMPLIANT |
| 5 | Latest-wins read | Later record wins | `tests/test_calibration.py:139` `test_latest_record_wins_by_recorded_at_and_ties_break_to_the_last_line` | ✅ COMPLIANT |
| 6 | Staleness downgrades, never hides | Revision mismatch → hard-stale, still shown with counts and date | `tests/test_calibration.py:343` (`[hard-stale]`, date :355, counts :356 kept) + matrix `:226` | ✅ COMPLIANT |
| 7 | Staleness downgrades, never hides | Aged record (100d) → soft-stale, still shown | `tests/test_calibration.py:359` (advisory `[soft-stale]`) + `:245` (91d soft) and `:250` (90d fresh boundary) | ✅ COMPLIANT |
| 8 | Verbatim identifier keys | Key miss → "no record" for that id only, no fuzzy match | `tests/test_calibration.py:174` `test_key_miss_never_fuzzy_matches_a_similar_id` + `:317` (`'828D'` in notice) | ✅ COMPLIANT |
| 9 | Dense-only floor documented | Documented scope | `tests/test_calibration.py:383` (`__doc__` contains "dense branch", "text branch", "ungated") + `src/plant_rag/calibration.py:8-11` | ✅ COMPLIANT |
| 10 | Serving non-participation | Serving ignores records | Anti-import guard `tests/test_calibration.py:433` + source scan (no `calibration` refs in `retrieve/`/`answer/`) + precedence tests `tests/test_answer.py:287-294` | ✅ COMPLIANT |
| 11 | Serving non-participation | Import guard | `tests/test_calibration.py:433` `test_serving_packages_never_import_the_calibration_store` (passed at runtime) | ✅ COMPLIANT |
| 12 | Suggestion content contract | Fresh record suggestion (floors, revision, date, counts, label, token, wipe) | `tests/test_calibration.py:266` (all fields, `separates this labeled set` :277, wipe :278-279, no "optimal" :280) + `:330` advisory; no-write contract `:370` and `tests/test_ingest.py:684` | ✅ COMPLIANT |
| 13 | Opt-in calibration recording in eval | No flag, no writes | `tests/test_scripts.py:268` `test_eval_without_record_writes_no_file` (exit 0, no file); eval takes the pre-change path when `args.record` is false (static diff) | ✅ COMPLIANT |
| 14 | Opt-in calibration recording in eval | Record appends exactly one line; MIN_SCORE untouched; report-only line still prints | `tests/test_scripts.py:279` (1 line :307-309, `config.MIN_SCORE == 0.40` :313, report lines present, no advisory line :296-298); report-only line `scripts/eval.py:861` is unconditional in the same returned string as the asserted `Recommendation:` line | ✅ COMPLIANT |
| 15 | Opt-in calibration recording in eval | Structured golden counts | `tests/test_scripts.py:306` (`golden_pass == 2`, `golden_total == 2`); production mapping `scripts/eval.py:627-628` itself untested → W2 | ✅ COMPLIANT |
| 16 | Opt-in calibration recording in eval | Disabled store with record flag | `tests/test_scripts.py:329` `test_eval_record_with_empty_path_is_silent_and_exit_unchanged` (exit 0, tmp empty, report unchanged) | ✅ COMPLIANT |
| 17 | Advisory after reindex | Fresh record → suggestion with revision, date, counts; config/store/index unchanged | `tests/test_ingest.py:614` (summary first, `[fresh]`, token, `(revisión r3)`, no wipe when floors match) + date/counts via `tests/test_calibration.py:266`; immutability `tests/test_ingest.py:684` | ✅ COMPLIANT |
| 18 | Advisory after reindex | No record → notice for that id | `tests/test_ingest.py:634` `test_ingest_no_record_prints_the_no_record_notice` | ✅ COMPLIANT |
| 19 | Advisory after reindex | Stale record (stored revision differs) → stale warning, not presented as current | hard-stale wording only at unit level `tests/test_calibration.py:343`; CLI test `tests/test_ingest.py:650` seeds the SAME revision with 100-day age (soft-stale path) → revision mismatch not exercised end-to-end on the ingest CLI → W1 | ⚠️ PARTIAL |
| 20 | Advisory after reindex | Disabled store → nothing printed, no file read | `tests/test_ingest.py:667` `test_ingest_with_empty_path_prints_nothing_extra` (plus `tests/test_calibration.py:198` `advisory("") is None`) | ✅ COMPLIANT |

**Compliance summary**: 20/20 scenarios have a passing covering test (19 ✅ COMPLIANT + 1 ⚠️ PARTIAL); 0 FAILING, 0 UNTESTED. Requirements: 9/10 fully compliant, 1/10 with one partial.

### Design Coherence (Engram design id 2818)

| # | Decision | Followed? | Evidence |
|---|---|---|---|
| 1 | Dense-only doc in module docstring + `__doc__` test | ✅ | `src/plant_rag/calibration.py:8-11`; `tests/test_calibration.py:383` |
| 2 | Key = `manual_id`, revision compared after read | ✅ | `src/plant_rag/calibration.py:145-158` filters by id; `:174` compares revision |
| 3 | `--record` silent append, no new printed lines | ✅ | `scripts/eval.py:1005-1009` (no print); `tests/test_scripts.py:296-298` |
| 4 | Golden counts as additive optional `EvalResult` fields | ✅ | `scripts/eval.py:128-129` (defaults `None`); `__str__` untouched |
| 5 | `calibration.py` stdlib only, no `config` import | ✅ | imports at `calibration.py:30-33` (json/datetime/pathlib/collections only) |
| 6 | Spanish text with verbatim tokens; "optimal" never | ✅ | `calibration.py:214,221`; `tests/test_calibration.py:280,391-395` |
| 7 | `staleness(..., current_label_totals)`; v1 ingest passes revision only | ✅ | `calibration.py:161-193` (drift branch), called at `:273` with revision only; unit test `:230` |
| 8 | `--record` mutually exclusive with `--no-calibration` (exit 2); append only if all checks pass and sweep has points | ✅ | `scripts/eval.py:926-940`, `:1005`; `tests/test_scripts.py:319` (exit 2), `:344` (failing check → no file) |
| 9 | ISO-8601 UTC `+00:00` (never `Z`) | ✅ | `tests/test_calibration.py:98` |

### Issues Found

**CRITICAL**: none

**WARNING** (severity counts: CRITICAL 0 · WARNING 2 · SUGGESTION 2)

- **W1** — manual-ingestion scenario "Stale record" is PARTIAL at CLI level: `tests/test_ingest.py:650-665` seeds `revision="r3"` with `result_revision="r3"` and `age_days=100`, so the ingest CLI is only proven on the soft-stale (age) path. The spec's GIVEN ("stored revision differs from the new revision") is covered only by the unit test `tests/test_calibration.py:343`.
- **W2** — new production mapping `scripts/eval.py:627-628` (`golden_pass=len(successes) if passed else None`) has no covering test: `tests/test_scripts.py:246` replaces `check_golden_grounding` with a fake that injects the counts, so the real success/failure-count wiring is proven only by static reading.

**SUGGESTION**

- **S1** — spec R9 says `--record` "SHALL append exactly one calibration record", but the gate at `scripts/eval.py:1005` also requires `report.points`; a fully passing eval with an empty sweep appends nothing (design decision 8). Document this in the answering delta or define an empty-sweep record.
- **S2** — add two hermetic tests closing W1 (CLI advisory with mismatched revision) and W2 (unit test of `check_golden_grounding` count mapping); no source change required.

### Pending release evidence

None claimed by this phase: live runtime-harness evidence (Qdrant/Ollama stacks) is out of the verify scope defined by the orchestrator; the hermetic suite is the entrypoint gate and it is green.

### Verdict

**PASS WITH WARNINGS** — 12/12 tasks, 326/326 tests exit 0 (two fresh runs), build exit 0, 20/20 scenarios covered by passing tests (19 fully compliant, 1 partial), 0 FAILING/0 UNTESTED, all 9 design decisions followed, `retrieve/`/`answer/`/web/`reindex.py` git-proven untouched, eval diff additive-only.

Validator: `gentle-ai sdd-verify-validate --input <candidate> --requirements 10 --scenarios 20` → result recorded in the phase return envelope.
