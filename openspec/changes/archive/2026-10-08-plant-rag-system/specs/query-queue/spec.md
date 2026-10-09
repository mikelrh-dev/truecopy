# query-queue Specification

## Purpose

Serialize generation on a single worker: FIFO order, rule-based safety preemption, aging against starvation, and a 45s end-to-end timeout with queue-position feedback.

## Requirements

### Requirement: FIFO single-worker processing

Queries SHALL be handled by one worker in arrival order.

#### Scenario: Two routine queries

- GIVEN two non-safety queries arrive in order
- WHEN the worker is free
- THEN they are answered in arrival order

### Requirement: Rule-based safety preemption

A query matching the closed set of safety terms MUST be served before waiting non-safety queries. Prioritization SHALL be rule-based over closed terms; no LLM safety classifier is used.

#### Scenario: Safety query jumps the queue

- GIVEN non-safety queries are waiting
- WHEN a safety-term query arrives
- THEN it is answered before them
- AND the displaced queries keep their relative order

#### Scenario: Ordinary query keeps FIFO

- GIVEN a non-safety query is waiting
- WHEN another non-safety query arrives
- THEN FIFO order is preserved between them

### Requirement: Aging against starvation

Waiting queries SHALL gain scheduling priority as wait time grows, so no non-safety query starves under sustained safety traffic.

#### Scenario: Long-waiting query is served

- GIVEN a non-safety query has waited through repeated safety preemptions
- WHEN its wait crosses the aging threshold
- THEN it is scheduled ahead of newer non-safety queries

### Requirement: Queue position and 45s timeout

While waiting, the operator SHALL see their queue position in Spanish. The end-to-end limit of 45s (queue + answer, per A1) SHALL, when exceeded, return a Spanish timeout message; no answer is fabricated.

#### Scenario: Position visible

- GIVEN three queries are queued
- WHEN an operator submits a query
- THEN the UI shows the query's queue position in Spanish

#### Scenario: Timeout message

- GIVEN a query reaches 45s end-to-end
- WHEN the timeout fires
- THEN the operator receives a Spanish timeout message
- AND no partial or invented answer is returned
