# Exploration: Decline-strong-pool detection

Change: `decline-strong-pool-detection` — MEJORAS-RAG.md item 4, **phase 1**
(detection only). Date: 2026-10-10.

## Current State (verified against code)

- The answering chain already has a bounded citation retry and two decline
  paths (`src/plant_rag/answer/llm.py`): an explicit refusal reply
  (`_is_refusal_text`), and a self-decline where the model attempted no
  citation marker (`CitationViolation` with no `_MARKER_RE` match).
- `QueryLog` hosts TWO event schemas already: answer records and
  `{"event": "feedback"}` votes. `records()` branches on any `event` key before
  touching `_FIELDS`, so a third schema is tolerated by construction.
- Today a refusal next to a full page-diverse pool leaves NO trace: the
  information ("the evidence was there; the model still refused") is thrown
  away. That is exactly the class the feedback loop exists to surface.
- `scripts/feedback_report.py` already reads the log with a `--days`/`--top`
  window and deterministic `topic_key` reduction; a detection view is a sibling
  of the vote view.

## Constraint that shapes the change

Generic pressure on a model that had already declined caused a **measured
incident**: an out-of-corpus question (cooking pasta) was "answered" from an
unrelated page (the horn page) after a nudge (`llm.py:208-214`). So phase 1
records evidence and changes no behavior; a directed, named-passage retry is
phase 2 and needs its own cycle plus the evidence this phase produces.

## Affected surface

`monitoring.py` (schema + writer + reader), `llm.py` (signal seam),
`pipeline.py` (binding), `scripts/feedback_report.py` (`--signals`), tests.
