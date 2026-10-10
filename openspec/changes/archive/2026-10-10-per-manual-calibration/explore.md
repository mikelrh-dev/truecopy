## Exploration: Per-manual calibration (closing provisional MIN_SCORE=0.40)

Change: `per-manual-calibration` — MEJORAS-RAG.md item 3.
Store mode: hybrid (this file + Engram `sdd/per-manual-calibration/explore`).
Date: 2026-10-10. All line references verified against the working tree.

### Current State

Verified facts (not assumptions):

- **Serving retrieval is hybrid**: `answer/pipeline.py:133` calls `hybrid_search`.
  - Dense branch carries the floor: `Prefetch(score_threshold=config.MIN_SCORE)` (`hybrid.py:106`).
  - Text branch (`hybrid.py:110-118`) has **no threshold** — token match + revision filter only.
  - `store.search` drops below-floor dense hits (`store.py:239-244`) but is **not** on the serving path; it is used by the eval sweep and by unit tests.
- `config.MIN_SCORE = 0.40`, env override `PLANT_RAG_MIN_SCORE`, validated in `[-1, 1]` at import (`config.py:96-100`); the comment marks it provisional pending eval recalibration.
- `scripts/eval.py` sweeps 0.10–0.70 (`eval.py:756`) with SEPARATE in-corpus / out-of-corpus columns and recommends the **highest threshold that keeps all in-corpus queries above the floor while all out-of-corpus queries stay below it** (`eval.py:811-834`). It ends with "The eval did NOT change config.MIN_SCORE." — report-only, by contract.
- **Sweep/serving mismatch (discovery)**: `run_calibration` probes `store_search` (dense-only) while production runs `hybrid_search`. The recommendation is a dense-path measurement; the text branch is never swept.
- **Label set is statistically weak (discovery)**: in-corpus queries are auto-generated from corpus English phrases (`_spanish_question_for_english_text`, `eval.py:733-736`) — easy, corpus-derived positives; out-of-corpus is 5 fixed questions. n≈5+5 across 7 scanned thresholds. No CI is possible at that n; by the rule of three, 0/5 out-of-corpus hits still leaves a ~60% upper 95% bound on the true false-positive rate. The recommendation must travel with its evidence (sweep points + totals), never as a bare number, and must be worded "separates this labeled set", not "optimal".
- Golden sets: `GOLDEN_SETS` keyed by `manual_id` + `expect_alarms` (`eval.py:184-229`); a loaded manual with no recorded set FAILS loudly (`eval.py:546-569`). Golden pass counts exist only inside `EvalResult.detail` strings — a pass-rate field needs structured counts from `check_golden_grounding`.
- **manual_id namespace split (discovery)**: `scripts/ingest.py` / `scripts/reindex.py` use `manual_id = PDF stem or --manual-id`; `web/app.py:270,313` hardcodes `manual_id = "manual-actual"` (asserted by `tests/test_owner_ops.py:180`). `GOLDEN_SETS` keys are PDF stems, so web-ingested manuals already miss their golden set — calibration records keyed by `manual_id` inherit the same split.
- Two ingestion entry points both call `run_reindex` directly: the CLI wrapper (`scripts/ingest.py:79-96`, prints a Spanish summary) and the web owner upload (`web/app.py:260-289`, shows `st.status`). A shared suggestion helper must serve both.
- Persistence precedents: append-only JSONL (`logs/queries.jsonl`, `logs/answer-cache.jsonl` — both gitignored), `to_json()` record classes in `monitoring.py`, `--json` flag in `scripts/feedback_report.py` (fails loudly on unreadable log).
- Constraints: local-only, stdlib-first, no new services; `logs/` and `outputs/` are gitignored. Answering spec pins `scripts/eval.py` to be unmodified **by the answer-cache feature** (`tests/test_pipeline.py:291-293` asserts no `AnswerCache` / `cache=` strings — keep them out). Answer-cache v1 invalidates on answer-relevant config change **by wiping the file** (`config.py:118-126`).
- Tests: 284 pass (verified this session, 23.5s), hermetic doubles in `tests/conftest.py` (FakeEmbedder, retrieval-order rerank), `FakeQdrant` with `query_points`/`scroll` in `tests/test_retrieve.py`.

### Affected Areas

- `scripts/eval.py` — record building/writing (explicit `--record` flag), structured golden pass counts; additive only, exit codes and the 4 checks unchanged.
- `scripts/ingest.py` — print the calibration suggestion after a successful reindex.
- `web/app.py` — v1: not modified; `_do_upload` leaves a seam for a later `st.info` (and is the source of the `manual-actual` id split).
- `src/plant_rag/calibration.py` (new) — stdlib-only record model, read/write, staleness, suggestion text. NOT imported by `retrieve/` or `answer/`.
- `src/plant_rag/config.py` — new `CALIBRATION_PATH` (env-overridable, `""` disables), following `QUERY_LOG_PATH` / `ANSWER_CACHE_PATH`.
- `src/plant_rag/retrieve/hybrid.py` — untouched in v1 (text branch stays ungated, documented).
- Tests: new `tests/test_calibration.py`; touch `tests/test_ingest.py` (suggestion output contract), `tests/test_scripts.py` (eval flag smoke).

### Q1 — Persistence options (per consumer)

| Option | Ingest suggest | Eval compare | Operator read | Verdict |
|---|---|---|---|---|
| **A. Local JSONL record file** (`CALIBRATION_PATH`, default `logs/calibration.jsonl`) | file read, stdlib, no stack needed | read + append on `--record`, hermetic with `tmp_path` | open the file / `--json` | **Recommended** — matches queries/answer-cache precedent, machine-local by design |
| B. config.py per-manual dict | env contract is flat scalars; a measured, dated record is **data, not config**; would blur "owner applies" | breaks `_env_float` pattern | editing code | Reject |
| C. Qdrant payload/collection | needs live stack | hermetic tests need fake collections; deleting a collection silently loses history | needs a query | Reject — couples report artifacts to serving DB |
| D. Report-only (no persistence) | impossible | reprint every run only | terminal scrollback | Fallback only — fails the item-3 requirement ("persist per manual_id") |
| E. OpenSpec/Engram as store | wrong layer (SDD artifacts, not runtime data) | — | — | Reject |

Location tradeoff: `logs/` is gitignored → records are machine-local measurements (consistent with local-only design; a fresh clone legitimately has none). A git-tracked file would give an audit trail of owner decisions but commits machine/model-specific numbers into the repo. Recommend `logs/` + `CALIBRATION_PATH` env override; the eval printout remains the human-readable audit.

### Q2 — Record schema and staleness

Append-only JSONL, one record per measurement, **latest record for `(manual_id)` wins at read time** (reader keeps the newest line):

```json
{
  "schema": 1,
  "manual_id": "Owners_Manual",
  "revision": "r8",
  "recommended_min_score": 0.45,
  "current_min_score": 0.40,
  "in_corpus_hits": 5, "in_total": 5,
  "out_of_corpus_hits": 0, "out_total": 5,
  "golden_pass": 7, "golden_total": 7,
  "golden_set_present": true,
  "sweep": [[0.10, 5, 1], [0.20, 5, 1], [0.30, 5, 0], [0.40, 5, 0],
            [0.50, 4, 0], [0.60, 3, 0], [0.70, 2, 0]],
  "separates_labeled_set": true,
  "recorded_at": "2026-10-10T12:00:00Z",
  "source": "scripts/eval.py --record"
}
```

Field rationale: `revision` + `recorded_at` make staleness checkable; `*_total` carries the sample size so confidence is judgeable; `sweep` preserves the evidence (the recommendation is derived, the points are the data); `golden_pass/golden_total` is the item-3 "pass de goldens por revisión"; `separates_labeled_set` is false when no threshold separated the classes (the record still gets written, with `recommended_min_score` = current default).

**Staleness rule** (checked in this order, all advisory — a stale record is downgraded in wording, never hidden):

1. **Revision mismatch (hard)**: `record.revision != current revision` → "measured on rX, serving rY — re-run the eval". Re-embedding rebuilds scores; a record never speaks for a revision it did not measure.
2. **Label-set drift (hard)**: current eval `in_total`/`out_total`/`golden_total` differ from the record's → "labeled set changed since this record — re-record".
3. **Age (soft)**: `recorded_at` older than ~90 days → warning line only. Corpus and model are local and pinned, so age alone is weak evidence; it must never invalidate a revision-matched record.

### Q3 — Ingest-time suggestion UX

Shared helper (new `src/plant_rag/calibration.py`): `load_records(path)`, `latest_for(manual_id)`, `suggest(manual_id, revision, current_min_score) -> str | None`. Pure string/file math, no Qdrant, no network.

CLI (`scripts/ingest.py`, after the existing OK block; operator-facing copy stays Spanish per project convention):

```
— Calibración MIN_SCORE — manual 'Owners_Manual' en r9
  Registro: suelo recomendado 0.45 (medido 2026-10-02 en r8, goldens 7/7, 5/5 en corpus, 0/5 fuera)
  Vigente:  PLANT_RAG_MIN_SCORE sin fijar → default 0.40
  Acción del owner (el sistema no cambia nada): exporte PLANT_RAG_MIN_SCORE=0.45
  Al aplicar un suelo nuevo borre logs/answer-cache.jsonl (contrato v1 de la caché).
```

Variants (stable substrings, testable with `capsys`):
- no record → `Sin calibración grabada para 'X' — ejecute scripts/eval.py y grabe la recomendación (--record).` (informational, exit code unchanged)
- stale record (revision/label drift) → prefix `AVISO: registro de rX servido en rY…`
- `CALIBRATION_PATH=""` → no output at all.

Web owner surface: **not in v1** (one operator, CLI-first; the web upload path also uses the divergent `manual-actual` id). Leave the seam: because `_do_upload` calls `run_reindex` in-process, a later `st.info(suggest(...))` is one line — no redesign needed. Flag the id split to the proposal rather than silently matching.

### Q4 — Text-branch gating options

| Option | Mechanism | Refusal-gate consequence | Effort |
|---|---|---|---|
| **a. Reuse `MIN_SCORE` on the text prefetch** | `score_threshold` on `hybrid.py:110-118` | **Category error**: text-branch scores are Qdrant full-text relevance, not BGE-M3 cosine — the same number means a different thing. Likely drops most text hits → smaller pools → **more false refusals** (the failure mode operators feel first), or does nothing if the scale is larger. Unmeasured either way. | Low code / High risk |
| **b. Separate `TEXT_MIN_SCORE`, measured by a hybrid-path sweep** | new config + sweep must run `hybrid_search` per branch | Correct, but two knobs for one operator, doubles the calibration surface, and the sweep change touches the release harness | High |
| **c. Post-fusion client-side gate** | filter after RRF | Payloads carry no vectors (`with_vectors=False`) and RRF output is rank-shaped; re-litigates the refusal contract in the pipeline. Reject. | Medium |
| **d. Leave ungated + document (recommended)** | status quo | Out-of-corpus safety net stays where it already is: eval check 3 + the `expect_refusal` golden + weekly thumbs-down on refusals. Document in the record/report that the floor is dense-branch-only. | Low |

Recommendation: **(d)** for this change — same philosophy as MEJORAS item 4 phase 1 (detect before intervening). If the feedback loop later shows text-branch-driven invention escaping refusal, option (b) becomes a separate change with its own measured threshold.

### Q5 — Recalibration triggers and precedence

Triggers — all **soft**, nothing runs automatically:

1. **Ingest / reindex** (CLI or web upload): print the suggestion (read-only).
2. **On demand**: `scripts/eval.py` keeps sweeping every run (existing behavior); a new explicit `--record` flag appends the record. Never implied by the default run — writing is an owner action, like applying is.
3. **Feedback signal**: weekly `feedback_report.py` refusal topics are the human prompt to re-run the eval. v1: no code coupling (defer; note in proposal as optional follow-up).

Precedence:

- **Serving**: `PLANT_RAG_MIN_SCORE` env > `config` default 0.40. **Records never participate in serving resolution** — that is the design enforcement of "el eval informa, el owner aplica".
- **Advisory (suggestion wording only)**: revision-matched fresh record > revision/label-stale record (with AVISO) > no record (current default, neutral prompt).

### Q6 — Risks and mitigations

| Risk | Why real | Mitigation |
|---|---|---|
| Auto-apply temptation (serving code reads the record) | one import away from "helpful" | `calibration.py` imported only by `scripts/` + tests; add a test asserting `retrieve/` and `answer/` never import it; keep eval's "did NOT change config.MIN_SCORE" line |
| Stale record served as truth | revision/labels drift | staleness rule (Q2): revision + date + counts always printed with the suggestion; downgrade wording, never hide |
| Statistical overconfidence (n≈5+5, easy positives, 7 thresholds scanned) | rule of three: 0/5 ⇒ ~60% upper bound | store `sweep` + totals in every record; suggestion shows counts and date; wording "separates this labeled set" |
| `manual-actual` vs PDF-stem id split | web upload hardcodes the id; goldens already miss | lookup misses → "no record" path (safe); flag id normalization as an explicit proposal decision, do not fuzzy-match |
| Eval contract drift (it is the release harness) | additive-flag changes can ripple | additive only: same 4 checks, same exit codes, same sweep lines; keep `AnswerCache`/`cache=` strings out (pinned test); no behavior change without `--record` |
| Applying a floor leaves a stale answer cache | documented v1 contract is wipe-on-config-change | suggestion text prints the wipe instruction |
| Per-manual complexity for one operator (5 manuals planned) | record sprawl | single JSONL, latest-wins, no indexes, no DB |

### Q7 — Test seams

- **Hermetic sweep**: `run_calibration(client, embedder)` already takes its dependencies; `FakeQdrant` (scroll + query_points) + `conftest.FakeEmbedder` exercise record building with zero network. Keep I/O out of the record builder: `build_record(report, golden_counts, manual_id, revision, now) -> dict` is pure.
- **Golden-record round-trip**: write → `latest_for` → assert fields; append a second line → latest wins; malformed line → defined behavior (recommend: writer raises `ValueError`, reader warns and treats as "no record" so ingest never breaks).
- **Ingest suggestion contract**: monkeypatch `run_reindex` + point `CALIBRATION_PATH` at `tmp_path`, `capsys` asserts the stable Spanish substrings (mirrors `tests/test_ingest.py` / `test_owner_ops.py` patterns).
- **Eval flag contract**: `--record` appends exactly one JSON line and does NOT mutate `config.MIN_SCORE`; without the flag nothing is written; entrypoint `--help` smoke stays in `tests/test_scripts.py`.
- **Guard rail**: test that `src/plant_rag/retrieve/*` and `src/plant_rag/answer/*` do not import `plant_rag.calibration` (the anti-auto-apply seam).

### Approaches

1. **Report-only (minimal)** — no persistence; docs explain how to read the sweep.
   - Pros: zero new surface, zero risk. Cons: fails item 3 outright (no per-manual persistence, no ingest suggestion). Effort: Low.
2. **Record file + suggest helper (recommended)** — `CALIBRATION_PATH` JSONL, new `plant_rag/calibration.py`, eval `--record` + structured golden counts, ingest prints the suggestion, text branch documented-ungated, serving untouched.
   - Pros: closes the provisional 0.40 with evidence, satisfies "eval informs / owner applies" structurally (serving cannot read the record), hermetic tests, ~4 files + tests, well inside the 400-line budget (likely 1 PR). Cons: eval.py gains an additive flag; id split flagged, not fixed. Effort: Medium.
3. **Full calibration subsystem** — approach 2 + hybrid-path sweep, `TEXT_MIN_SCORE`, web `st.info` surface, `manual_id` normalization.
   - Pros: measures what actually serves. Cons: doubles the knobs for one operator, touches the release harness deeply and the web id contract (which has its own test), high review surface now. Effort: High. Split into later changes if evidence demands it.

### Recommendation

**Approach 2**, with these decisions handed to the proposal:

- Persist to `CALIBRATION_PATH` (default `logs/calibration.jsonl`), append-only, latest-record-wins, schema as in Q2.
- Serving code never reads it — precedence env > default stays exactly as today; the record only feeds human-facing suggestions (Q5).
- Suggestion is a shared `suggest()` helper printed by `scripts/ingest.py` in Spanish; web surface deferred, seam preserved (Q3).
- Text branch stays ungated in v1, documented in the record header/report; option (b) is a follow-up only if feedback shows need (Q4).
- Staleness = revision match + label totals + soft age, downgrade-not-hide (Q2).
- Eval changes are additive (`--record`, structured golden counts), exit codes and existing output lines unchanged.
- Open question for the proposal: whether to normalize `manual-actual` (web) to the PDF-stem namespace now or record `manual-actual` as its own key and document the split.

### Risks

Top three (full table in Q6): (1) a future change lets serving read the record, silently reintroducing the automatism item 3 forbids; (2) a stale/weak-evidence record is quoted as truth; (3) eval drift breaks the release harness. Mitigations are testable, not aspirational: the no-import guard, mandatory revision/date/counts in every suggestion line, and the additive-flag contract test.

### Ready for Proposal

Yes. Scope is concrete and bounded (one new module, one config key, additive eval flag, one CLI print, tests), the anti-automaticity rule is structurally enforceable, and the only open decision is the `manual_id` namespace question above.
