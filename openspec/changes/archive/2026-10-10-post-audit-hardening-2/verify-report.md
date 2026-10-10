---
title: Verify Report — post-audit-hardening-2
type: architecture
created: 2026-10-10
updated: 2026-10-10
change: post-audit-hardening-2
verdict: pass
---

# Verify Report: post-audit-hardening, round 2

**Change**: post-audit-hardening-2 (5 medium + 6 low findings from the system audit)
**Branch**: `docs/roadmap` (working tree; not yet committed)
**Verified on**: 2026-10-10

## Verdict

**PASS** on the hermetic and configuration evidence. **Not verified** at the time
of this report: the live end-to-end run (Qdrant was down). That gap was later
closed by the incident documented in `2026-10-10-zero-vector-index-guard`, which
also proved that the pool gate of M3 fails closed exactly as specified.

## Gates

| Gate | Result | Command / evidence |
|---|---|---|
| Full suite | PASS | `.venv/Scripts/python.exe -m pytest tests -q` → **362 passed**, exit 0 (was 355) |
| Lint | PASS (delta) | `ruff check src web scripts tests` → **32**, all `E402` in `scripts/` (was 51; every F-class finding gone) |
| Compile | PASS | `.venv/Scripts/python.exe -m compileall -q src web scripts tests` → exit 0 |
| Entrypoint | PASS | `.venv/Scripts/python.exe scripts/eval.py --help` → exit 0 after the import surgery |
| Qdrant exposure | PASS | `docker compose config` renders both ports with `host_ip: 127.0.0.1` (2 occurrences) |
| Auth repro (M1) | PASS | Live `AuthStore`: exact form with spaces authenticates; trimmed form raises `WrongPasswordError` |
| Bootstrap repro (L2) | PASS | Live `create_default_users`: prints once; `admin-early-boot-owner` raises `WrongPasswordError` |
| Live end-to-end | NOT RUN | Qdrant down (`127.0.0.1:6333` closed) at verification time; see the zero-vector change |

## Per-finding compliance

| # | Requirement | Evidence | Verdict |
|---|---|---|---|
| M1 | Password compared byte-for-byte | `web/auth.py` authenticate; `tests/test_web.py::test_authenticate_accepts_a_password_with_surrounding_spaces`; live repro | PASS |
| M2 | Sweep measures the serving path | `scripts/eval.py::run_calibration` (expansion + `retrieve_pool`); `tests/test_scripts.py::test_run_calibration_probes_the_serving_pool_not_the_dense_path` | PASS |
| M3 | Pool-level floor | `retrieve/hybrid.py` dense probe; `tests/test_retrieve.py::test_hybrid_requires_dense_backing_above_the_floor`, `::test_hybrid_fuses_when_the_dense_branch_has_above_floor_evidence`; integration doubles honor `score_threshold` | PASS |
| M4 | Qdrant on loopback | `docker-compose.yml`; README + TUTORIAL notes; `docker compose config` | PASS |
| M5 | Cache invalidates by construction | `answer/cache.py` fingerprint in key and record; `tests/test_answer_cache.py::test_a_config_change_invalidates_prior_entries`; advisory wording in `calibration.py` + `tests/test_calibration.py` | PASS |
| L1 | Dead code / F821 removed | `ruff --statistics`: only `E402` remains; `web/app.py::_do_reindex` no longer opens a connection | PASS |
| L2 | No fixed bootstrap credential | `web/auth.py::create_default_users`; 3 tests in `tests/test_web.py` | PASS |
| L3 | Dependency source documented | README quick-start note (`requirements.txt`, ADR-002) | PASS |
| L4 | First-run egress documented | README quick-start note (BGE-M3 + reranker, one time) | PASS |
| L5 | UI poll / answer map bounded safely | `web/app.py` oldest-first eviction, 0.25 s poll | PASS |
| L6 | Stale Python 3.10 caches | Declared not actionable (gitignored); no change made | PASS (no action) |

## Spec compliance

| Domain | Action | Evidence in the living spec |
|---|---|---|
| `retrieval` | Added | "Pool-level relevance floor" (2 scenarios) matches `hybrid_search`'s probe gate |
| `answer-cache` | Modified | A1/A2 + "Config change misses" match the fingerprint in `cache.py` |
| `min-score-calibration` | Modified | "Floor scope and sweep path documented" + suggestion contract match `eval.py` and `calibration.py` |
| `web-access-auth` | Modified/Added | Password fidelity scenario + "No fixed bootstrap credential" match `auth.py` |

## Honest limits

- The pool gate and the cache fingerprint are proven against test doubles that
  now mirror the real Qdrant signature; they were not exercised against a live
  server in this change.
- Latency was not measured here: the live probe of the next change measured
  retrieval + rerank at 6.9–8.9 s with double work (two pools per question), so
  no serving latency claim is made in this report.
- `ruff` is not a project dependency (no config in `pyproject.toml`, not in
  `requirements.txt`); the counts above come from a local binary, used as an
  audit tool rather than a CI gate.
