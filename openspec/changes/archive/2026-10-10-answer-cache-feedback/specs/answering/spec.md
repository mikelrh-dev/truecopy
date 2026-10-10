# Delta for answering

## MODIFIED Requirements

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
