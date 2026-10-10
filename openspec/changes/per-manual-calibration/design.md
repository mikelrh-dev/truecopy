# Design: Per-manual calibration

## Technical Approach

Proposal approach 2: stdlib-only `calibration.py` of pure record/suggestion seams, wired additively. `eval.py --record` appends exactly one JSONL line from the sweep plus structured golden counts; `ingest.py` prints a read-only advisory after reindex; `config.CALIBRATION_PATH` (default `logs/calibration.jsonl`, `""` disables) follows the `QUERY_LOG_PATH`/`ANSWER_CACHE_PATH` pattern. Serving never touches records — anti-import guard test over `retrieve/` and `answer/`. Specs: min-score-calibration, answering, manual-ingestion.

## Architecture Decisions

| # | Decision | Choice | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| 1 | Dense-only floor doc home | `calibration.py` module docstring + `__doc__` substring test | Standalone doc file | Spec scenario reads "calibration module documentation"; docstring is verifiable and adds no file |
| 2 | Lookup key | `manual_id` only; revision compared after read | Composite (id, revision) key | Revision mismatch must surface as hard-stale, never a silent miss |
| 3 | `--record` output | Silent append; no new lines in either mode | Confirmation line | Requirement pins printed lines unchanged in both modes; eval is the release harness |
| 4 | Golden counts | Additive optional `EvalResult.golden_pass/golden_total` (default None) | Parsing `detail` strings; tuple returns | `__str__` and every call site stay byte-identical |
| 5 | `calibration.py` deps | stdlib only; path/revision/cache-path passed as params | `from_config()` reading `config` | Hermetic seams; guard trivially satisfied; scripts wire config |
| 6 | Suggestion language | Spanish text with verbatim tokens `separates this labeled set`, `fresh`/`soft-stale`/`hard-stale` | All-Spanish / all-English | Spanish advisory + exact wording substrings; "optimal" never appears |
| 7 | Label-drift staleness | `staleness(..., current_label_totals=None)`; v1 ingest passes revision only | Import `scripts/eval.GOLDEN_SETS` from ingest | No script→script imports; drift branch unit-tested |
| 8 | `--record` gating | Mutually exclusive with `--no-calibration` (exit 2); append only if all checks pass and sweep has points | Silent skip / late error | Spec pins one line per successful eval; unknown flags already exited 2 |
| 9 | Timestamps | ISO-8601 UTC `+00:00` (never `Z`) | `Z` suffix | Active interpreter is 3.10: `fromisoformat` there rejects `Z` |

Append mirrors existing JSONL stores: `mkdir(parents=True)`, `"a"` mode, `ensure_ascii=False`, corrupt lines skipped on read (`monitoring.py`, `answer/cache.py` precedent).

## Data Flow

    eval.py --record ─(sweep + golden counts)→ build_record() ─append_record─→ logs/calibration.jsonl
                                          (one line; config.MIN_SCORE untouched)
    ingest.py after reindex OK ─ advisory(path, id, revision) ─ latest_record → staleness → suggest
                                  └ path=="" → None → prints nothing      (read-only ES print)

    retrieve/ and answer/ ──✗── plant_rag.calibration        (guard test)

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/plant_rag/calibration.py` | Create | v1 schema, append/read/latest-wins, staleness, `suggest()`, `advisory()`, dense-only docstring |
| `src/plant_rag/config.py` | Modify | `CALIBRATION_PATH` via `_env_str` (new end-of-file section) |
| `scripts/eval.py` | Modify | `--record`, `_best_separating()` extraction (output unchanged), golden count fields, append call |
| `scripts/ingest.py` | Modify | post-summary advisory print |
| `tests/test_calibration.py` | Create | contracts, staleness matrix, guard, docstring scope |
| `tests/test_scripts.py` | Modify | `--record` one-line/no-mutation, combo exit 2 |

## Interfaces / Contracts

```python
# calibration.py — stdlib only, every input explicit
build_record(...) -> dict        # pure; schema v1 keys: manual_id, revision,
   # recommended_min_score (None iff no separating point), current_min_score,
   # in/out_corpus_hits+totals, golden_pass/total, golden_set_present,
   # sweep[{threshold,in_hits,out_hits}], separates_labeled_set, recorded_at, source
append_record(path, record) -> bool          # "" → False; mkdir parents; byte-append
read_records(path) -> list[dict]             # "" → []; corrupt lines skipped
latest_record(path, manual_id) -> dict|None  # max recorded_at, tie → last line
staleness(rec, *, current_revision, current_label_totals=None, now=None)
                                             # hard (rev/label) > soft (>90d) > fresh
suggest(rec, label, *, cache_path) -> str    # ES contract; wipe instruction iff floors differ
advisory(path, manual_id, revision, *, cache_path) -> str|None  # None iff disabled
```

Suggestion contract: recommended+current floor, revision, record date, golden pass/total, staleness label; stale prints carry counts+date (downgrade, never hide).

## Testing Strategy

| Layer | What | Approach |
|-------|------|----------|
| Unit | Round-trip, append-never-rewrites, latest-wins, staleness matrix, suggestion/advisory text contracts | `tmp_path` JSONL, pure calls, substring asserts |
| Guard | `retrieve/`+`answer/` never import `plant_rag.calibration` | source scan (precedent: `test_pipeline.py` source asserts) |
| Integration | One line appended, `MIN_SCORE` unchanged, combo exit 2, ingest fresh/stale/no-record/silent variants | fake report + `tmp_path`; monkeypatched `run_reindex` |
| Contract | Eval output/exit codes unchanged; `"cache="`/`"AnswerCache"` absent from eval source | pinned tests + source asserts |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary (a data-file read/write plus two prints).

## Migration / Rollout

No migration: new file, additive flag, advisory print; `""` disables everything. Rollback = revert 3 files, delete `calibration.py` + JSONL; serving never changed. `logs/` is gitignored.

## Open Questions

- [ ] `scripts/reindex.py` (replacement CLI) not wired in v1 — proposal pins `ingest.py`; delta says "after a successful CLI reindex". Confirm at tasks.
- [ ] None blocking.
