# Delta for manual-ingestion

## ADDED Requirements (proposed — not yet applied to the living spec)

### Requirement: Encoder output is real evidence

Every embedded child SHALL carry a vector that the encoder actually produced.
The write path MUST reject an all-zero vector, failing loudly with the offending
`child_id`, before any point is upserted and before any `current_*` alias moves;
a rejected ingest MUST leave the previously aliased revision serving and discard
the incomplete one. A vector of the correct dimensionality whose values are not
all zero SHALL proceed unchanged.

Rationale: a revision whose vectors are all zero validates, aliases and serves
the exact refusal for every question, which is indistinguishable from a
well-behaved out-of-corpus answer to the operator. Failure must be loud at
ingest time, not silent at query time.

#### Scenario: Zero vector fails the ingest

- GIVEN an encoder that returns an all-zero vector for a child
- WHEN the owner ingests the manual
- THEN the ingest fails with the offending `child_id` and no alias moves
- AND the revision in service before the attempt still answers

#### Scenario: A real vector proceeds

- GIVEN an encoder returning vectors of `config.EMBED_DIMS` with non-zero values
- WHEN the owner ingests the manual
- THEN the revision is built, count-validated and aliased as before
