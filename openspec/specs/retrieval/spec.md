# retrieval Specification

## Purpose

Map Spanish operator queries to English manual content (cross-lingual ES→EN), including an exact alarm-code path, scoped to the current revision, returning page numbers for the answer contract.

## Assumptions

- **A2** — Exact alarm-code lookup is a guaranteed fast path through a dedicated alarm-table index, not merely semantic retrieval.

## Requirements

### Requirement: Cross-lingual ES→EN retrieval

The system SHALL retrieve English manual passages for Spanish queries and MUST return each passage's page number. Retrieval covers text content and alarm tables only (images are out of phase 1).

#### Scenario: Spanish query finds English passage

- GIVEN an indexed English manual
- WHEN an operator submits a Spanish query that the manual answers
- THEN relevant English passages are returned with their page numbers

#### Scenario: Query outside the corpus

- GIVEN no passage matches the query
- WHEN retrieval runs
- THEN an empty result set is returned and nothing is synthesized

### Requirement: Exact alarm-code fast path

The system SHALL resolve exact alarm-code queries against a dedicated alarm-table index and MUST return the matching entry regardless of semantic similarity.

#### Scenario: Known alarm code

- GIVEN the alarm table contains code `12345` with its description
- WHEN the operator queries `12345`
- THEN the matching alarm entry is returned with its page number
- AND resolution does not depend on embedding similarity

#### Scenario: Unknown alarm code

- GIVEN the alarm table contains no such code
- WHEN the operator queries the code
- THEN no alarm entry is returned, feeding the refusal path

### Requirement: Pool-level relevance floor

The dense branch SHALL apply the calibrated relevance floor, and the fused candidate pool MUST be empty unless the dense branch produced at least one above-floor candidate. A full-text match alone MUST NOT admit a passage: the text branch is a recall aid on top of dense evidence, so a token coincidence can never reach the prompt. An empty pool feeds the exact refusal with no LLM call.

#### Scenario: Text match without dense backing

- GIVEN a passage whose full-text tokens match the query but whose dense score is below the floor
- WHEN retrieval runs
- THEN the pool is empty and no passage is returned

#### Scenario: Dense-backed fusion

- GIVEN at least one passage above the floor
- WHEN the dense and full-text branches are fused
- THEN the pool carries the dense-backed candidates in `Fusion.RRF` rank order

### Requirement: Current-revision scope

Retrieval SHALL search only chunks of the current manual revision.

#### Scenario: After a reindex

- GIVEN a new revision is current
- WHEN a query runs
- THEN no chunk from a previous revision is returned
