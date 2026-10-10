# manual-ingestion Specification

## Purpose

Load the single current manual into the local index with bounded memory, page-level provenance, and revision tracking. The owner performs upload and reindex themselves (no admin backoffice).

## Assumptions

- **A3** — A full-reindex service window is acceptable: queries MAY pause during reindex. Design MUST confirm before implementation.

## Requirements

### Requirement: Batched ingestion with provenance

The system SHALL ingest the manual PDF (phase 1: Siemens 828D, text only) in bounded batches within the 16GB RAM budget. Ingestion SHALL produce parent-child chunks, and every child chunk MUST retain manual identifier, page number, and parent linkage.

#### Scenario: Owner ingests the manual

- GIVEN the owner has the Siemens 828D PDF available locally
- WHEN ingestion is executed
- THEN every page is indexed with retrievable page numbers
- AND peak memory usage stays within the 16GB budget

#### Scenario: Corpus does not fit one batch

- GIVEN the full manual exceeds a single batch's memory bound
- WHEN ingestion runs
- THEN batches are processed sequentially to completion without data loss

### Requirement: Revision metadata from day one

Every indexed unit SHALL carry `revision` metadata identifying the corpus version. Ingesting a replacement manual SHALL assign a new revision, and only the newest revision SHALL be marked current.

#### Scenario: First ingestion

- GIVEN no index exists
- WHEN the manual is ingested
- THEN all indexed units carry an initial revision value

#### Scenario: Replacement manual

- GIVEN a replacement manual has been ingested
- WHEN a query runs afterward
- THEN results originate from the new revision only

### Requirement: Full reindex only (single current manual)

The system SHALL support full reindex as the only update mode; the index SHALL always represent exactly one current manual. Chunks from a prior index MUST NOT remain retrievable after reindex completes.

#### Scenario: Reindex replaces the index

- GIVEN an index built from a previous manual
- WHEN the owner triggers a full reindex
- THEN the completed index contains only the new corpus
- AND stale chunks are no longer returned

#### Scenario: Reindex interrupted

- GIVEN a reindex fails partway through
- WHEN an operator queries the system
- THEN results never mix chunks from the old and new corpora

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
