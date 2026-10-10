# answer-cache Specification

## Purpose

Serve repeat operator questions from a revision-scoped answer cache so identical questions return at near-zero latency without LLM cost, while preserving the answer contract and full eval isolation.

## Assumptions

- **A1** — Cache identity is (corpus revision, model, normalized question); a reindex or model swap invalidates all prior entries by construction. No TTL or manual flush UI in v1.
- **A2** — v1 documents wipe-on-config-change as the invalidation contract for answer-relevant config changes (min-score, glossary, safety terms, prompt). A config fingerprint is deferred; documenting the wipe is in scope, implementing fingerprints is not.
- **A3** — Cache storage is disposable, gitignored operator data; loss or corruption never degrades answering.

## Requirements

### Requirement: Revision-scoped cache hit

A cache hit SHALL return the stored answer immediately, bypassing retrieval and generation. The stored payload MUST be byte-identical to the answer originally produced for the same key, preserving citations, refusal text, and embedded safety warnings. The hit SHALL be logged truthfully with its real near-zero latency.

#### Scenario: Repeat question hits cache

- GIVEN a question was answered and stored under the current revision and model
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

### Requirement: Miss path stores safely

On a miss the system SHALL run the normal answering chain and store the result only if the corpus revision is unchanged at store time. Timeouts and LLM errors MUST NOT be stored. Refusals and safety answers SHALL be stored. A store failure MUST NOT break, delay, or alter the returned answer.

#### Scenario: Miss then store

- GIVEN no stored entry for the key
- WHEN the chain returns an answer and the revision is unchanged
- THEN the answer is stored for future hits

#### Scenario: Revision changed mid-answer

- GIVEN a miss whose chain completed across a revision swap
- WHEN the post-answer revision check finds the revision changed
- THEN nothing is stored and the produced answer is still delivered

#### Scenario: LLM error or timeout

- GIVEN the chain raises an LLM error or exceeds the deadline
- WHEN the exception propagates to the caller
- THEN no cache entry is written

#### Scenario: Store failure is best-effort

- GIVEN the cache cannot persist (disk error, unreadable path)
- WHEN a miss answer is returned
- THEN the answer is delivered normally and no cache error surfaces to the operator

### Requirement: Operator traffic only

Only CLI and web operator traffic SHALL read from or write to the cache. The evaluation harness MUST NOT read or write the cache. The hermetic test suite MUST include an automated test enforcing that eval traffic never touches the cache.

#### Scenario: Eval runs cache-disabled

- GIVEN the evaluation set runs while an operator cache exists
- WHEN any question is evaluated
- THEN no cache entry is served or written

### Requirement: Corrupt entries skipped

Corrupt or unreadable cache records SHALL be skipped; one bad record MUST NOT prevent other entries from serving.

#### Scenario: Corrupt record among valid entries

- GIVEN the cache contains one corrupt record and several valid ones
- WHEN the cache is loaded
- THEN all valid entries remain usable
