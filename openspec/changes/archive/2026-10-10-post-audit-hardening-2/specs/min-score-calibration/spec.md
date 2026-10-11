# Delta for min-score-calibration

## MODIFIED Requirements

### Requirement: Floor scope and sweep path documented

Calibration documentation SHALL state that the recommended floor scores the
dense branch (full-text scores are not cosine scores) and that the retrieval
pool is empty without at least one above-floor dense candidate (the text branch
is a recall aid, never independent evidence). The sweep SHALL probe the SERVING
path — the same ES→EN expansion and hybrid pool builder that answers operators —
not a dense-only search.

#### Scenario: Documented scope

- GIVEN the calibration module documentation
- WHEN it describes the recommended floor
- THEN it states dense-branch scoring, the pool-level gate, and that the sweep measures the serving path

#### Scenario: Sweep counts serving hits

- GIVEN a labeled set and a candidate floor
- WHEN the sweep probes it
- THEN in-corpus and out-of-corpus hits are counted from the serving pool builder, with the question embedded in expanded form

### Requirement: Suggestion content contract

Every suggestion line MUST carry recommended and current floor, revision, record
date, golden pass/total counts, staleness label, and wording "separates this
labeled set" (never "optimal"). A differing recommendation MUST state the env
override to apply and that prior cached answers stop being served on their own
(the cache key carries a config fingerprint; no manual wipe is required).
Suggestions MUST NOT write to config, store, or index.

#### Scenario: Differing recommendation names the override

- GIVEN a record whose recommended floor differs from the current one
- WHEN the suggestion line is produced
- THEN it names the env override to apply and states that the cache invalidates by construction
