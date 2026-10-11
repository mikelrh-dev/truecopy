# Delta for manual-ingestion

## ADDED Requirements

### Requirement: One identity namespace for ingested manuals

The ingested `manual_id` SHALL be derived from the manual's filename by one
shared helper, sanitized to `[0-9A-Za-z_]` with a deterministic fallback, and
MUST be identical for every entrypoint (the ingest CLI, the reindex CLI, and the
web upload). The filesystem path MUST NOT carry the identity; staged uploads keep
their UUID name. The same PDF MUST always land in the same identity namespace so
golden sets, calibration records, and citations agree.

#### Scenario: Two uploads get two identities

- GIVEN two owner uploads with different filenames
- WHEN each is ingested
- THEN each is recorded under its own filename-derived id

#### Scenario: The filename never reaches the staged path

- GIVEN an upload whose name contains path traversal
- WHEN it is staged
- THEN the staged path stays inside the staging directory under a UUID name
- AND the traversal string appears only in the sanitized id

## MODIFIED Requirements

### Requirement: Advisory calibration notice after reindex

After a successful CLI reindex — for BOTH the first-load entrypoint
(`scripts/ingest.py`) and the replacement entrypoint (`scripts/reindex.py`) —
the system SHALL print a read-only Spanish advisory derived from the calibration
store for the ingested id: fresh record prints the suggestion per the calibration
suggestion contract; stale record prints a stale warning; no record prints a
brief no-record notice; empty `CALIBRATION_PATH` prints nothing. The advisory
MUST NOT modify the index, config, or store. The web upload path shares the
identity namespace but does not print the advisory in this version.

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

#### Scenario: Replacement CLI carries the advisory

- GIVEN the replacement CLI (`scripts/reindex.py`) completes a successful swap
- WHEN it finishes
- THEN it prints the same advisory as the first-load CLI, after the swap summary
