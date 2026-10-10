# min-score-calibration Specification

## Purpose

Persist eval floor recommendations and golden counts per manual/revision; the owner applies them manually. Eval informs; serving never auto-applies.

## Scope Boundaries

Web deferred: v1 covers CLI reindex only; the web upload path MUST NOT change; the suggestion helper SHALL stay callable later.

## Requirements

### Requirement: Append-only record schema v1

The store SHALL be append-only JSONL of v1 records, never mutated or rewritten. Each record MUST carry: manual_id, revision, recommended_min_score, current_min_score, in/out-of-corpus hits and totals, golden_pass/golden_total, golden_set_present, sweep points, separates_labeled_set, recorded_at, source.

#### Scenario: Record round-trip

- GIVEN a built v1 record
- WHEN it is appended and read back
- THEN every schema field is present and unchanged

#### Scenario: Append never rewrites

- GIVEN an existing record file
- WHEN a new record is appended
- THEN prior lines stay byte-identical

### Requirement: Configurable store path

The store path SHALL come from `CALIBRATION_PATH` (default `logs/calibration.jsonl`); an empty value MUST disable reads and writes as silent no-ops.

#### Scenario: Default path

- GIVEN no environment override
- WHEN eval records a run
- THEN one line appends to `logs/calibration.jsonl`

#### Scenario: Disabled store

- GIVEN `CALIBRATION_PATH=""`
- WHEN eval records or an ingest advisory runs
- THEN nothing is written and nothing is printed

### Requirement: Latest-wins read

Reading SHALL return the most recent record for a key; earlier records for that key MUST be ignored.

#### Scenario: Later record wins

- GIVEN two records for one manual_id and revision
- WHEN the store is read
- THEN the latest recorded_at is returned

### Requirement: Staleness downgrades, never hides

Staleness SHALL be: revision mismatch or label-total drift hard stale; age over 90 days soft stale. Stale records MUST surface with downgraded wording, never be suppressed; every suggestion line MUST carry revision, record date, and golden counts.

#### Scenario: Revision mismatch

- GIVEN a record for a previous revision
- WHEN a suggestion is produced
- THEN it is labeled hard-stale yet shown with counts and date

#### Scenario: Aged record

- GIVEN a matching record recorded 100 days ago
- WHEN a suggestion is produced
- THEN it is labeled soft-stale and still shown

### Requirement: Verbatim identifier keys

Records SHALL key on the ingested id verbatim. There is ONE identity namespace — the sanitized filename — shared by the CLI entrypoints and the web upload, so a manual ingested from the web and the same manual ingested from the CLI key on the same id (the previous split where the web used `manual-actual` is removed). A miss MUST fail clean to "no record" and MUST NOT fuzzy-match to a similar key.

#### Scenario: Key miss

- GIVEN no record exists for the requested id
- WHEN a suggestion is requested
- THEN "no record" is reported for that id only

#### Scenario: Web and CLI share the key

- GIVEN a manual ingested from the web under its filename-derived id
- WHEN the CLI ingests the same manual
- THEN both resolve to the same id and the same calibration record

### Requirement: Floor scope and sweep path documented

Calibration documentation SHALL state that the recommended floor scores the dense branch (full-text scores are not cosine scores) and that the retrieval pool is empty without at least one above-floor dense candidate (the text branch is a recall aid, never independent evidence). The sweep SHALL probe the SERVING path — the same ES→EN expansion and hybrid pool builder that answers operators — not a dense-only search.

#### Scenario: Documented scope

- GIVEN the calibration module documentation
- WHEN it describes the recommended floor
- THEN it states dense-branch scoring, the pool-level gate, and that the sweep measures the serving path

### Requirement: Serving non-participation

Calibration records MUST never participate in serving threshold resolution; precedence stays `PLANT_RAG_MIN_SCORE` env over config default. The retrieval and answer packages MUST NOT import the calibration module, enforced by a guard test.

#### Scenario: Serving ignores records

- GIVEN a record recommends a new floor
- WHEN a query is served
- THEN the effective floor is unchanged

#### Scenario: Import guard

- GIVEN the retrieval and answer packages
- WHEN the guard test runs
- THEN neither imports `plant_rag.calibration`

### Requirement: Suggestion content contract

Every suggestion line MUST carry recommended and current floor, revision, record date, golden pass/total counts, staleness label, and wording "separates this labeled set" (never "optimal"). A differing recommendation MUST state the env override to apply and that prior cached answers stop being served on their own (the cache key carries a config fingerprint; no manual wipe is required). Suggestions MUST NOT write to config, store, or index.

#### Scenario: Fresh record suggestion

- GIVEN a fresh record for the ingested id
- WHEN a suggestion is produced
- THEN it shows recommendation, current floor, revision, date, counts, and cache-wipe instruction
