```yaml
schema: gentle-ai.verify-result/v1
evidence_revision: sha256:40a4e02a2c2c161e969a2aee0dff96b81499a091555a3932430715bcf0e69a8a
verdict: pass
blockers: 0
critical_findings: 0
requirements: 5/5
scenarios: 16/16
test_command: .venv/Scripts/python.exe -m pytest tests -q
test_exit_code: 0
test_output_hash: sha256:cf1f6c5a3f252d95c85e07a73c6a15fa00699e1f1fc0373c50411470fcc10cf2
build_command: .venv/Scripts/python.exe -m compileall -q src web scripts tests
build_exit_code: 0
build_output_hash: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

# Verify Report: post-audit-hardening

**Change**: post-audit-hardening (audit findings A1/A2 + advisory coverage + eval note)
**Mode**: Standard (strict TDD inactive)
**Repo**: branch `feat/post-audit-hardening` — 4 code commits on `6de5ce4`
(`ac63437`, `338251e`, `a0cfb71` detection, `94282bc`) plus the identity-helper
test; working tree clean at verification start
**Scope**: report-only — no source, test, spec, or task file modified by this report

### Completeness

Envelope basis: complete = scenario with a passing covering test at runtime
(COMPLIANT); FAILING/UNTESTED = 0. Fully-compliant subtotal 16/16.

- Tasks: all `- [x]` in `tasks.md` (zero `- [ ]`)
- Spec counts read from the delta specs: `manual-ingestion` 1 added + 1 modified,
  `min-score-calibration` 1 modified, `answering` 1 added + 1 modified →
  **5 requirements / 16 scenarios**

### Build & Tests Execution

- Build: `.venv/Scripts/python.exe -m compileall -q src web scripts tests` →
  **exit 0**, 0 output bytes (`sha256:e3b0c442…`)
- Tests: `.venv/Scripts/python.exe -m pytest tests -q` → **355 passed, 5
  warnings, exit 0** (was 346 on the base branch; +9 = 7 planned + 2 identity-helper
  tests added during verify). `test_output_hash` over the exact captured
  stdout+stderr bytes. Warnings = 5 pre-existing SWIG DeprecationWarnings
- Hermeticity: no live Qdrant/Ollama required

### Requirement Coverage

| # | Requirement (delta) | Scenarios | Covering test(s) | Verdict |
|---|---|---|---|---|
| 1 | manual-ingestion · One identity namespace | two uploads; filename never on the path | `test_owner_ops.py::test_two_different_uploads_get_two_different_manual_ids`, `::test_the_uploaded_filename_never_reaches_the_staged_path`, `test_ingest.py::test_manual_id_from_filename_is_the_single_sanitized_identity` | ✅ COMPLIANT |
| 2 | manual-ingestion · Advisory after reindex (modified) | fresh; no record; stale; disabled; replacement CLI | `test_ingest.py::test_ingest_prints_fresh_advisory_after_the_summary`, existing no-record/stale/disabled cases, `::test_reindex_cli_prints_the_same_calibration_advisory` | ✅ COMPLIANT |
| 3 | min-score-calibration · Verbatim identifier keys (modified) | key miss; web and CLI share the key | `test_calibration.py` miss case; `test_owner_ops.py` + `test_ingest.py::test_ingest_cli_derives_its_id_from_the_filename_when_no_flag` | ✅ COMPLIANT |
| 4 | answering · Logged answer provenance | stamps revision; cached hit; harness reads none | `test_pipeline.py::test_a_logged_answer_records_the_corpus_revision`, `::test_a_cached_hit_also_logs_its_revision`; `test_pipeline.py` cache-disabled revision test | ✅ COMPLIANT |
| 5 | answering · Opt-in calibration recording in eval (modified) | no flag; record appends; nothing written; disabled | `test_scripts.py` record cases + `::test_eval_record_without_a_record_says_why_on_stderr` | ✅ COMPLIANT |

### Verify-Scope Isolation Evidence

| Check | Command / evidence | Result |
|---|---|---|
| No serving-behavior change | `git diff 6de5ce4..HEAD -- src/plant_rag/retrieve` | empty (0 files) |
| Refusal contract intact | `test_pipeline.py` full-pool refusal + `test_answer.py` retry tests | pass |
| Eval stdout frozen | `test_scripts.py::test_eval_record_without_a_record_says_why_on_stderr` | stdout byte-identical to the no-flag run |
| Identity is not the staged path | `test_owner_ops.py::test_the_uploaded_filename_never_reaches_the_staged_path` | staged path under the staging dir (UUID name) |

### Findings

- **0 CRITICAL**, 0 blockers, 0 warnings.
- The multi-manual follow-up must add `manual_id` to the answer-cache key; this
  change deliberately leaves the cache key as (revision, model, question).

### SDD Cycle

Specs written and synced, tasks complete, verified green, ready to archive.
