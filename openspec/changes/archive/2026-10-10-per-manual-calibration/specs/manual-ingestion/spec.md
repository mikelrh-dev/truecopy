# Delta for manual-ingestion

## ADDED Requirements

### Requirement: Advisory calibration notice after reindex

After a successful CLI reindex, the system SHALL print a read-only Spanish advisory derived from the calibration store for the ingested id: fresh record prints the suggestion per the calibration suggestion contract; stale record prints a stale warning; no record prints a brief no-record notice; empty `CALIBRATION_PATH` prints nothing. The advisory MUST NOT modify the index, config, or store. The web upload path is deferred and MUST NOT change.

#### Scenario: Fresh record

- GIVEN a fresh record matches the ingested id and revision
- WHEN reindex completes
- THEN the advisory prints the suggestion with revision, date, and counts
- AND config, store, and index are unchanged

#### Scenario: No record

- GIVEN no record exists for the ingested id and the store is enabled
- WHEN reindex completes
- THEN a no-record notice is printed for that id

#### Scenario: Stale record

- GIVEN the stored revision differs from the new revision
- WHEN reindex completes
- THEN the advisory prints the stale warning and does not present the value as current

#### Scenario: Disabled store

- GIVEN `CALIBRATION_PATH=""`
- WHEN reindex completes
- THEN nothing is printed and no file is read
