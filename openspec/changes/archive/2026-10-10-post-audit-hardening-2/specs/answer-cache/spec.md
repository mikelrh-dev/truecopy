# Delta for answer-cache

## MODIFIED Requirements

### Requirement: Revision-scoped cache hit

A cache hit SHALL return the stored answer immediately, bypassing retrieval and
generation. The stored payload MUST be byte-identical to the answer originally
produced for the same key, preserving citations, refusal text, and embedded
safety warnings. The key SHALL be (corpus revision, model, answer-relevant
config fingerprint, normalized question), so an entry written under a different
config is never served. The hit SHALL be logged truthfully with its real
near-zero latency.

#### Scenario: Repeat question hits cache

- GIVEN a question was answered and stored under the current revision, model and config
- WHEN the same question is asked again in normalized-equivalent form
- THEN the stored answer is returned without retrieval or LLM generation
- AND the query log records the hit with its real near-zero latency

#### Scenario: Normalized variants share an entry

- GIVEN an entry stored for `¿Qué es el TIA?`
- WHEN the operator asks `que es el tia`
- THEN the lookup hits the same entry

#### Scenario: Different revision misses

- GIVEN a stored answer for revision rN
- WHEN the current corpus revision is rN+1
- THEN the lookup misses and the full answering chain runs

#### Scenario: Different model misses

- GIVEN a stored answer produced by model A
- WHEN the same question is asked while model B is active
- THEN the lookup misses and the full answering chain runs

#### Scenario: Config change misses

- GIVEN a stored answer for the current revision and model
- WHEN an answer-relevant config value changes (min-score, prompt, safety terms, glossary, branch sizes)
- THEN the lookup misses and the full answering chain runs
- AND no manual cache wipe is required to reach that state
