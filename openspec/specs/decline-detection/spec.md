# decline-detection Specification

## Purpose

Surface the suspicious refusal — the model declining while the pool of page-diverse evidence was full — so the operator can inspect what the model saw before refusing. Detection only: the event changes no answer, threshold, or retry path. A later, evidence-gated phase may decide on a directed retry; this capability exists to produce that evidence.

## Assumptions

- **A1** — Detection only. The event never changes an answer, a threshold, or the retry logic. Phase 2 (a directed, named-passage retry) requires its own SDD cycle and measured evidence; a bad retry costs trust.
- **A2** — "Strong pool" is the heuristic `len(hits) >= config.TOP_K`: the page-diverse candidates actually handed to the LLM. It is not a quality judgement.
- **A3** — Only operator traffic signals. The signal is bound to the operator query log exactly like the answer record; eval/harness traffic passes no log and therefore never emits a signal.
- **A4** — The event shares the operator query log with answer and feedback lines under the same lock. No second file exists; every reader already branches on the event type before touching answer fields.
- **A5** — The log lives on the operator's machine (gitignored path); nothing about the signal leaves it.

## Requirements

### Requirement: Decline-strong-pool event schema

The system SHALL append a `{"event": "decline-strong-pool"}` JSONL line to the operator query log under the same lock as answer and feedback lines, carrying timestamp, question, pool size, page list, manual id, corpus revision, and model. A failed write MUST be best-effort and MUST NOT raise or block the answer.

#### Scenario: Event round-trip

- GIVEN a full pool the model declined on
- WHEN the signal is recorded and read back
- THEN the event carries its pool size, pages, manual id, revision, and model

#### Scenario: A failed signal write never blocks

- GIVEN the log path cannot be written
- WHEN a decline signal is recorded
- THEN no exception propagates to the operator

### Requirement: Signal on every decline next to a full pool

A decline SHALL signal when the pool is full on each path that produces a refusal: an explicit refusal reply, a self-decline that attempted no citation, and a decline on the bounded retry (the pool is unchanged, so it is the same case). A thin pool MUST stay silent: a refusal with little evidence is plausible.

#### Scenario: First decline with a full pool

- GIVEN a full pool and a reply with no citation
- WHEN the model declines
- THEN exactly one signal is recorded carrying the full pool

#### Scenario: Second decline after the retry

- GIVEN a full pool, a first reply whose citation marker is unparseable, and a retry that declines
- WHEN generation finishes
- THEN the retry runs once and one signal is recorded carrying the full pool

#### Scenario: Thin pool is plausible

- GIVEN fewer candidates than `config.TOP_K`
- WHEN the model declines
- THEN no signal is recorded

### Requirement: Answer contract unchanged

Recording a signal MUST NOT alter the returned answer, the refusal text, the evidence gate, the citation validator, or the retry rules. The event is written beside the answer, never into it.

#### Scenario: Contract preserved

- GIVEN a decline next to a full pool
- WHEN the signal is recorded
- THEN the returned answer is the exact refusal and the pool is untouched

### Requirement: Operator-log binding

The signal SHALL be emitted only when an operator log exists. A call with no log MUST forward no signal, so harness traffic is excluded by construction. Answer-record reads MUST skip any event line before accessing answer fields.

#### Scenario: Harness traffic never signals

- GIVEN an answering call with no operator log
- WHEN a full-pool decline happens
- THEN no signal is emitted on the generation seam

#### Scenario: Event lines never corrupt answer stats

- GIVEN a log containing answer, feedback, and decline-signal lines
- WHEN answer records are read
- THEN only answer lines count and event lines are skipped

### Requirement: Detection view in the triage report

`scripts/feedback_report.py --signals` SHALL list the decline-strong-pool events inside the window, ordered by count descending with alphabetical ties, showing the pool size and pages of the most recent occurrence per topic. The window defaults to the same last-7-days / top-10 contract as the vote view. An unreadable log MUST fail loudly (exit 1); an empty window is still a report (exit 0).

#### Scenario: Signals listed per topic

- GIVEN two decline events for one topic inside the window
- WHEN `--signals` runs
- THEN the topic shows its count plus the pool size and pages of the newest occurrence

#### Scenario: Unreadable log fails loudly

- GIVEN a log path that cannot be read
- WHEN `--signals` runs
- THEN it errors on stderr and exits 1
