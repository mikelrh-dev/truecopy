# Delta for answering

## ADDED Requirements (proposed — not yet applied to the living spec)

### Requirement: Phase timings in answer records

Each logged answer SHALL carry the per-phase timings of the phases that RAN
while producing it — embed, Qdrant, rerank and LLM seconds — alongside the
existing total latency. Fields are additive; units are seconds; the required
schema does not change. When nothing was timed, the record keeps its historical
shape: the `phases` key is ABSENT from the line, not null, so a reader that
predates this requirement reads the line unchanged.

Rationale: total latency alone cannot tune timeouts or catch degradations
(slow Ollama, a dead reranker hiding behind fallback order). This is the
instrument the E7 prompt-size measurement also rides on.

Measured values are stored unrounded; the total keeps its historical
three-decimal rounding. The sum of the phases is therefore bounded by the
recorded total up to that rounding.

#### Scenario: Timings present on a stubbed chain

- GIVEN a hermetic answering run with injected seams
- WHEN a semantic answer is recorded
- THEN the record carries non-negative `embed_s`, `qdrant_s`, `rerank_s` and
  `llm_s`
- AND their sum is no more than the recorded total latency (plus its rounding)

#### Scenario: An answer served from cache carries no phases

- GIVEN a cache hit, where no phase ran this turn
- WHEN the answer is recorded
- THEN the line carries no `phases` key at all

#### Scenario: A historical line still parses

- GIVEN a stored answer line written before this requirement
- WHEN the log is read back
- THEN the record parses with no phases recorded, and answer statistics are
  unchanged

### Requirement: Rerank-fallback signal

When ranking falls back to retrieval order (model unloadable or scoring
failure), the system SHALL record the fallback once per answer. The returned
order is unchanged; only observability is added.

Rationale: today's fallback is silent (`retrieve/rerank.py:91-102`), so an
operator can read unranked results indefinitely without anyone knowing.

#### Scenario: Fallback is recorded, order preserved

- GIVEN an injected failing scorer
- WHEN reranking runs
- THEN the incoming order is returned AND exactly one fallback signal exists

### Requirement: Chunk-level citations with page fallback

Citation markers MAY reference the winning child fragment
(`[Fuente: … Página N §child]`) in addition to the page-level marker, which
remains valid and remains the contract minimum. The validator accepts both;
an invented chunk fails closed exactly like an invented page.

Rationale: page-only cites are imprecise on long pages, and the child already
travels in every `Hit`. Cost is tokens, measured via the Batch D telemetry.

#### Scenario: Mixed markers validate

- GIVEN an answer citing one page marker and one chunk marker, both retrieved
- WHEN validation runs
- THEN both prove and the answer stands

#### Scenario: Invented chunk fails closed

- GIVEN a chunk marker for a fragment never retrieved
- WHEN validation runs
- THEN it raises `CitationViolation` and the caller refuses exactly

### Requirement: Bounded conversational memory

Follow-up questions MAY rewrite the query from bounded session history (max N
turns, small, configured): history is query-rewrite INPUT ONLY and MUST NEVER
reach the prompt builder as evidence. Citations resolve against current-turn
retrieval only; the deadline applies per turn; the refusal contract is
byte-unchanged.

Rationale: this is the step from answering to understanding. The shape of the
rule is what keeps it safe: history can change WHAT is asked, never what
counts as evidence.

#### Scenario: Follow-up resolves through rewrite

- GIVEN a previous turn about brakes
- WHEN the operator asks "¿y el delantero?"
- THEN retrieval runs on the rewritten query and the answer cites retrieved pages

#### Scenario: Planted history can never become a citation

- GIVEN session history containing a fake page number
- WHEN the operator asks anything
- THEN no citation in the answer may reference the planted number
