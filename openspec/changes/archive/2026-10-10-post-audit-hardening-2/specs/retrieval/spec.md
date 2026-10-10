# Delta for retrieval

## ADDED Requirements

### Requirement: Pool-level relevance floor

The dense branch SHALL apply the calibrated relevance floor, and the fused
candidate pool MUST be empty unless the dense branch produced at least one
above-floor candidate. A full-text match alone MUST NOT admit a passage: the
text branch is a recall aid on top of dense evidence, so a token coincidence
can never reach the prompt. An empty pool feeds the exact refusal with no LLM
call.

#### Scenario: Text match without dense backing

- GIVEN a passage whose full-text tokens match the query but whose dense score is below the floor
- WHEN retrieval runs
- THEN the pool is empty and no passage is returned

#### Scenario: Dense-backed fusion

- GIVEN at least one passage above the floor
- WHEN the dense and full-text branches are fused
- THEN the pool carries the dense-backed candidates in `Fusion.RRF` rank order
