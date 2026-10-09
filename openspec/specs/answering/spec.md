# answering Specification

## Purpose

Produce operator-Spanish answers under a strict contract: cite Manual + Page, never invent, refuse exactly, warn on safety — within the 45s end-to-end limit.

## Assumptions

- **A1** — A "<5s" response is a nice-to-have target; the contractual limit is the 45s timeout covering queue wait + generation.
- **A4** — The three mandatory checks start as manual spot-checks; a scripted eval set SHALL land once a test runner exists.

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

The system SHALL cover three mandatory checks: alarm-code retrieval, ES→EN retrieval, and refusal. These MAY run as manual spot-checks while no test runner exists; once a runner lands they MUST become a scripted eval set.

#### Scenario: No runner available

- GIVEN no test runner is configured
- WHEN release verification runs
- THEN the three checks are executed and recorded manually

#### Scenario: Runner available

- GIVEN a test runner is configured
- WHEN the eval set runs
- THEN all three checks execute automatically and MUST pass
