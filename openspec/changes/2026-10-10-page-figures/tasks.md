# Tasks: Page figures beside the cited excerpt

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~260 (code ~150, tests ~90, specs ~20) |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR, one work unit (extract → flag → render) |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: feature-branch
400-line budget risk: Low

Status: **implemented and hermetic green**; the live end-to-end run on the
production corpus is pending the source PDF.

### Suggested Work Units

| Unit | Goal | Focused test command | Rollback boundary |
|------|------|----------------------|-------------------|
| 1 | Figure extracted, flagged, and shown | `.venv/Scripts/python.exe -m pytest tests/test_ingest.py tests/test_web.py -q` | Revert `ingest/figures.py` + its wiring + tests |

## Phase 1: Extraction

- [x] 1.1 `config.FIGURES_DIR` (default `data/figures`; `""` disables)
- [x] 1.2 `ingest/loader.py`: `Page.has_figures` computed per page (no bytes)
- [x] 1.3 `ingest/figures.py`: largest embedded image → PNG, deterministic path, render fallback, best-effort

## Phase 2: Ingest wiring

- [x] 2.1 `ingest/reindex.py`: `has_figures` payload flag on child points
- [x] 2.2 Save flagged pages' figures before the swap; a failure never fails the ingest

## Phase 3: Web rendering

- [x] 3.1 `_cited_page_payloads` returns text + revision + `has_figures`
- [x] 3.2 `_cited_figure` resolves the path and only returns it when the file exists
- [x] 3.3 `_render_answer` shows the figure inside the cited page's excerpt

## Phase 4: Spec + verification

- [x] 4.1 Spec deltas (manual-ingestion, answering) applied to the living specs
- [x] 4.2 Tests: loader flag, path, save, disabled, reindex flag + file, failure-never-fails, web payload + figure
- [x] 4.3 `.venv/Scripts/python.exe -m pytest tests -q` → **381 passed**, exit 0; `ruff` clean on the touched files
- [ ] 4.4 Live end-to-end on the production manual (blocked: source PDF absent in this environment; re-ingesting would clobber r9)
