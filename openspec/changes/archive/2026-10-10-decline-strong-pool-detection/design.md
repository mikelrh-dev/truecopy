# Design: Decline-strong-pool detection (roadmap item 4, phase 1)

## Technical Approach

Stdlib-only detection welded onto existing seams. `monitoring.py` gains a third
event schema (`DeclineSignalRecord`, `record_decline_signal`, `event_records`)
next to the two it already hosts; `llm.py` gains an optional
`decline_signal: Callable[[str, tuple[Hit, ...]], None]` invoked on each decline
path when `_full_pool(hits)`; `pipeline.answer_question` binds the signal to the
operator `QueryLog` exactly like `log=`/`cache=`, so no log means no signal.

```
answer_question(question, ..., log=QueryLog)
        │  log is not None
        ▼
   decline_signal = lambda q, hits: log.record_decline_signal(q, hits, model=...)
        │
        ▼
   llm.generate(..., decline_signal=...)
        │  refusal path AND _full_pool(hits)
        ▼
   {"event": "decline-strong-pool", ts, question, pool_size, pages,
    manual_id, revision, model}   →  logs/queries.jsonl (same lock)
```

## Decision record

| # | Decision | Chosen | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| 1 | Where the event lives | The operator query log, third event schema | A second file | One append-only history; readers already tolerate event lines by branching on `event` before any field access |
| 2 | Trigger heuristic | `len(hits) >= config.TOP_K` | Any refusal | A refusal with little evidence is the expected, healthy case; only a full pool is suspicious |
| 3 | Seam shape | An optional callable mirroring `log=`/`cache=` | A module-level import of the log in `llm.py` | The `log=` seam already excludes harness traffic by construction; the signal inherits that guarantee and its test |
| 4 | Second decline included | Yes — the retry's refusal signals too | First decline only | The pool did not change between attempts; a model that declined twice next to full evidence is exactly the class this capability exists to surface |
| 5 | Reader coupling | `records()` skips ANY line with an `event` key, before field access | Whitelist the two known events | A future event type cannot be misread as a corrupt answer |
| 6 | Phase 2 (retry) | Out of scope | Directed retry now | Measured history: nudging a model that had declined made it answer an out-of-corpus question (cooking pasta) from the unrelated horn page. Detection costs nothing; a bad retry costs trust |

## Behavior is untouched

The signal is a *write beside* the answer. The evidence gate, the citation
validator, the bounded retry, the refusal text, and every threshold are
unchanged; `tests/test_pipeline.py` still pins the exact refusal next to a full
pool, and `tests/test_answer.py` pins that the retry still runs exactly once.

## Failure modes

| Failure | Behavior |
|---|---|
| Disk full / unwritable log | Swallowed; the answer is still returned (best-effort write) |
| Log contains answer + vote + signal lines | `records()` counts answers only; event lines are skipped before field access |
| No operator log (eval/harness) | No signal is forwarded at all — excluded by construction |
| A future event type | Same skip path as the two known events; no corruption possible |
