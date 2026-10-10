# answering Specification

## Purpose

Produce operator-Spanish answers under a strict contract: cite Manual + Page, never invent, refuse exactly, warn on safety — within the 45s end-to-end limit.

## Assumptions

- **A1** — A "<5s" response is a nice-to-have target; the contractual limit is the 45s timeout covering queue wait + generation.
- **A4** — The mandatory evaluation set is scripted (`scripts/eval.py`) and runs against a live stack; the hermetic pytest suite is a separate gate that needs neither Qdrant nor Ollama.

## Requirements

### Requirement: Cited Spanish answers, never invent

Every answer SHALL be written in operator Spanish, grounded only in retrieved passages, and each cited claim MUST reference Manual + Page. The system SHALL NOT generate content unsupported by retrieved evidence.

#### Scenario: Grounded answer

- GIVEN retrieved passages cover the question
- WHEN the operator asks in Spanish
- THEN the Spanish answer cites Manual + Page for its claims

#### Scenario: Evidence does not support an answer

- GIVEN retrieved passages do not cover the question
- WHEN answering would require speculation
- THEN the system refuses instead of inventing content

### Requirement: Exact not-found refusal

When the corpus does not contain the answer, the system SHALL reply with exactly `No aparece en la documentación`.

#### Scenario: Out-of-corpus question

- GIVEN no retrieved content answers the question
- WHEN an answer would be produced
- THEN the operator receives exactly `No aparece en la documentación`

### Requirement: Safety warnings

Answers on safety topics SHALL carry an operator-facing safety warning in Spanish, in addition to citations.

#### Scenario: Safety question answered

- GIVEN a query on a safety topic with supporting passages
- WHEN the answer is produced
- THEN it includes a Spanish safety warning
- AND cites Manual + Page

### Requirement: Response-time contract

The answer SHALL complete within the 45s end-to-end timeout (queue wait + generation). Reaching the limit MUST NOT emit a partial or fabricated answer. The <5s figure is a target only, per A1.

#### Scenario: Under budget

- GIVEN retrieval and generation finish normally
- WHEN the answer returns
- THEN it arrives within 45s with citations

#### Scenario: Budget exhausted

- GIVEN processing reaches the 45s limit
- WHEN the timeout triggers
- THEN no invented or partial answer is delivered; the timeout notice comes from the query-queue capability

### Requirement: Mandatory evaluation set

The system SHALL cover three mandatory checks: alarm-code retrieval, ES→EN retrieval, and refusal. The evaluation set SHALL run as a scripted eval set with the answer cache disabled: eval traffic MUST NOT read from or write to the operator answer cache, and the eval entry point (`scripts/eval.py`) SHALL remain unmodified by the answer-cache feature.

(Previously: the eval set had no cache-isolation clause, because no answer cache existed.)

#### Scenario: Runner available

- GIVEN the test runner is available
- WHEN the eval set runs
- THEN all three checks execute automatically and pass

#### Scenario: Eval never touches the operator cache

- GIVEN an operator answer cache exists
- WHEN the evaluation set runs end to end
- THEN no cache entry is read or written
- AND `scripts/eval.py` is unchanged by the answer-cache feature

### Requirement: Opt-in calibration recording in eval

The eval entry point SHALL accept an additive `--record` flag. Without `--record`, eval SHALL write NOTHING: no calibration file is created, read, or appended. With `--record`, eval SHALL append exactly one calibration record when the run passes AND the sweep produced a point, and MUST NOT mutate `config.MIN_SCORE`. When `--record` is given and no record is appended, the reason (failing checks, an empty sweep, or a disabled path) SHALL be explained on stderr; stdout MUST stay byte-identical to the run without the flag. In both modes, checks, printed lines, and exit codes SHALL remain unchanged, including the report-only line stating that eval did not change `config.MIN_SCORE`.

The record is appended only after every check passes AND the calibration sweep produced at least one threshold point: a fully passing eval whose sweep is empty appends nothing (design decision 8 — an empty sweep carries no separating threshold to recommend, and a record without one would claim otherwise).

#### Scenario: No flag, no writes

- GIVEN a configured calibration store path
- WHEN eval runs without `--record`
- THEN no calibration file is created or appended
- AND output, checks, and exit code match pre-change behavior

#### Scenario: Record appends exactly one line

- GIVEN eval completes successfully with `--record`
- WHEN the record is written
- THEN exactly one record line is appended
- AND `config.MIN_SCORE` is unchanged
- AND the report-only line still prints

#### Scenario: Structured golden counts

- GIVEN the golden checks have run
- WHEN the record is appended
- THEN it carries golden_pass and golden_total as structured fields

#### Scenario: Record requested but nothing written

- GIVEN `--record` and a run that cannot produce a record
- WHEN eval finishes
- THEN the reason is printed on stderr
- AND stdout is byte-identical to the same run without the flag
- AND the exit code is unchanged

#### Scenario: Disabled store with record flag

- GIVEN `CALIBRATION_PATH=""`
- WHEN eval runs with `--record`
- THEN nothing is written and checks, lines, and exit codes are unchanged

### Requirement: Logged answer provenance

When an answer is logged to the operator query log, the record SHALL carry the corpus revision that produced it (`r{N}`). The revision SHALL be read from the store once per operator question; a call with no operator log MUST NOT read the revision (eval and harness traffic pay nothing). A record whose revision cannot be resolved (no corpus) MAY carry the empty string, preserving the historical shape.

#### Scenario: Logged answer stamps its revision

- GIVEN an operator log and a current corpus revision rN
- WHEN a question is answered and logged
- THEN the record carries `revision="rN"`

#### Scenario: Cached hit stamps the same revision

- GIVEN a cache hit for the current revision
- WHEN the answer is logged
- THEN its record carries the same `revision` as the miss path would

#### Scenario: Harness traffic reads no revision

- GIVEN an answering call with no operator log
- WHEN it answers
- THEN no revision is read from the store
