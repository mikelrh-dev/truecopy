# Tasks: Post-audit hardening, round 2

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~470 (code ~150, tests ~180, specs/docs ~140) |
| 400-line budget risk | Medium (docs/specs push it over; code alone is inside) |
| Chained PRs recommended | Yes — 3 work units |
| Suggested split | S1 security (auth + bootstrap + Qdrant), S2 serving truthfulness (pool gate + cache fingerprint + sweep path), S3 hygiene (dead code + docs) |

Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: feature-branch with 3 revertible work units
400-line budget risk: Medium

Open questions resolved: (1) the pool gate fails closed and declares an empty
pool for a text-only match; (2) the cache wipe instruction is replaced by a
config fingerprint; (3) the bootstrap password is random per install.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | Nobody logs in through whitespace and no credential ships in the source | `.venv/Scripts/python.exe -m pytest tests/test_web.py -q` | Revert `web/auth.py` + `docker-compose.yml` |
| 2 | The floor, the cache key and the sweep describe the path that actually serves | `.venv/Scripts/python.exe -m pytest tests/test_retrieve.py tests/test_answer_cache.py tests/test_scripts.py tests/test_calibration.py -q` | Revert `hybrid.py`, `cache.py`, `calibration.py`, `scripts/eval.py` |
| 3 | Lint noise, UI polling and the docs stop contradicting the code | `.venv/Scripts/python.exe -m pytest tests -q` | Revert `web/app.py` + doc hunks |

## Phase 1: Security and access (M1, M4, L2)

- [x] 1.1 RED: `tests/test_web.py` — a password with surrounding spaces authenticates only in its exact form; the trimmed form is a different secret
- [x] 1.2 GREEN: `AuthStore.authenticate` verifies the raw secret (`password.strip()` only for the emptiness check)
- [x] 1.3 RED: `create_default_users` prints a random owner password once and the old literal is rejected; a second boot keeps the existing owner
- [x] 1.4 GREEN: `secrets.token_urlsafe(16)` + one-time console notice, `password=` injectable for tests
- [x] 1.5 `docker-compose.yml`: publish `127.0.0.1:6333` and `127.0.0.1:6334` with the rationale comment; README/TUTORIAL note the loopback scope

## Phase 2: Serving truthfulness (M2, M3, M5)

- [x] 2.1 RED: `tests/test_retrieve.py` — a token-only match whose dense score is below the floor yields an empty pool; an above-floor candidate fuses normally
- [x] 2.2 GREEN: `hybrid.hybrid_search` probes the dense branch (`limit=1`, `score_threshold=MIN_SCORE`) before fusing and returns `()` when it finds nothing
- [x] 2.3 Test doubles mirror the real client: `score_threshold` accepted and honored on plain queries (`test_retrieve.FakeQdrant`, `test_integration.FakeQdrantClient`)
- [x] 2.4 RED: `tests/test_answer_cache.py` — a config change makes prior entries miss without a manual wipe
- [x] 2.5 GREEN: `answer/cache.py` puts a config digest in the key and records `fingerprint`; lines from another config are skipped
- [x] 2.6 RED: `tests/test_scripts.py` — `run_calibration` probes the serving pool (expanded query) once per labeled query per threshold
- [x] 2.7 GREEN: `scripts/eval.py` embeds the expansion and counts hits via `retrieve_pool`
- [x] 2.8 `calibration.suggest` stops asking for a cache wipe: it states the env override and that the cache invalidates by construction (+ tests updated)

## Phase 3: Hygiene and docs (L1, L3, L4, L5, L6)

- [x] 3.1 Remove `web/app.py::_do_reindex`'s dead connection and unused import; keep the operator warning text
- [x] 3.2 `_ANSWERS` evicts the oldest entry instead of clearing the map; poll loop at 0.25 s
- [x] 3.3 `scripts/eval.py`: drop unused locals/imports (`position_*`, unused `embedder`, `store_search`) and the placeholder-only f-strings
- [x] 3.4 `web/app.py`: `TYPE_CHECKING` import for the `QueryLog` annotation instead of the runtime import
- [x] 3.5 README: `requirements.txt` is the dependency source (`pip install -e .` is not enough), first-run HF download, loopback Qdrant, random bootstrap credential
- [x] 3.6 TUTORIAL/ARQUITECTURA/ROADMAP: test count 355 → 362, bootstrap wording, cache-key note

## Phase 4: Specs re-synced

- [x] 4.1 `retrieval`: +"Pool-level relevance floor" (2 scenarios)
- [x] 4.2 `answer-cache`: A1/A2 (config fingerprint), +"Config change misses" scenario
- [x] 4.3 `min-score-calibration`: "Floor scope and sweep path documented"; suggestion contract now states the override + self-invalidation
- [x] 4.4 `web-access-auth`: +"Password with surrounding spaces" scenario, +"No fixed bootstrap credential" requirement

## Phase 5: Verification

- [x] 5.1 `.venv/Scripts/python.exe -m pytest tests -q` → 362 passed, exit 0
- [x] 5.2 `ruff check src web scripts tests` → 32 findings, all `E402` in `scripts/` (was 51)
- [x] 5.3 `.venv/Scripts/python.exe -m compileall -q src web scripts tests` → exit 0
- [x] 5.4 `docker compose config` renders both ports with `host_ip: 127.0.0.1`
- [x] 5.5 Live repro of M1 and L2 against the real `AuthStore`
