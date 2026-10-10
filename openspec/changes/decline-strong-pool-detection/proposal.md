# Proposal: Decline-strong-pool detection

## Intent

MEJORAS-RAG.md item 4, **phase 1 (detection only)**: the pipeline currently
throws away information when the model declines. A refusal next to a FULL
page-diverse pool is the suspicious case (the evidence was there; the model
still refused) — exactly the class the feedback loop was built to surface.
This change records that event. It changes NO answer, NO threshold, and NO
retry behavior.

## Scope

### In Scope
- `monitoring.py`: third event schema `{"event": "decline-strong-pool"}`
  (pool size, pages, manual, revision, model), `record_decline_signal()`,
  `event_records()` reader; `records()` branches on ANY event (a future
  event type cannot be misread as a corrupt answer).
- `llm.py`: optional `decline_signal` callable, invoked on the two decline
  paths when `len(hits) >= config.TOP_K` (detection heuristic only).
- `pipeline.py`: `_answer_question` forwards the signal; `answer_question`
  binds it to the operator log exactly like `log=`/`cache=` — no log → no
  signal, so eval/harness traffic is excluded by construction.
- `scripts/feedback_report.py`: `--signals` view (same window/ordering, pool
  and pages of the newest occurrence per topic).
- Hermetic tests for all of the above (+11).

### Out of Scope (phase 2, needs evidence and its own SDD cycle)
- NO directed retry, NO second Ollama call, NO change to the refusal
  contract, NO serving-side use of the signal. The measured history decides
  whether phase 2 exists.

## Why not a retry now

Pressure on the model caused the measured "cooking pasta answered with the
horn page" incident (llm.py:208-214). Detection costs nothing and produces
the evidence a retry decision would need.

## Risks

| Risk | Mitigation |
|---|---|
| Signal volume | Best-effort write, one line per decline-with-full-pool only |
| Reader schema coupling | Event branching happens BEFORE any field access |
| Harness pollution | Signal bound only when an operator log exists; test pins it |

## Rollback

Revert the four files; the log's event lines are inert for every existing
reader (they were already skipped as non-answer events).
