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

### Requirement: Encoder output is real evidence

Every embedded child SHALL carry a vector the encoder actually produced. The write path MUST reject an all-zero vector, failing loudly with the offending `child_id`, before any point is upserted and before any `current_*` alias moves; a rejected ingest MUST leave the previously aliased revision serving and discard the incomplete one. A vector of the correct dimensionality whose values are not all zero SHALL proceed unchanged.

Rationale: a revision whose vectors are all zero validates, aliases and serves the exact refusal for every question — indistinguishable to the operator from a well-behaved out-of-corpus answer (live incident, 2026-10-10). Failure must be loud at ingest, not silent at query time.

#### Scenario: Zero vector fails the ingest

- GIVEN an encoder that returns an all-zero vector for a child
- WHEN the owner ingests the manual
- THEN the ingest fails naming the offending `child_id`
- AND no alias moves and the incomplete revision is discarded

#### Scenario: A real vector proceeds

- GIVEN an encoder returning vectors of `config.EMBED_DIMS` with non-zero values
- WHEN the owner ingests the manual
- THEN the revision is built, count-validated and aliased as before

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

After a successful CLI reindex — for BOTH the first-load entrypoint (`scripts/ingest.py`) and the replacement entrypoint (`scripts/reindex.py`) — the system SHALL print a read-only Spanish advisory derived from the calibration store for the ingested id: fresh record prints the suggestion per the calibration suggestion contract; stale record prints a stale warning; no record prints a brief no-record notice; empty `CALIBRATION_PATH` prints nothing. The advisory MUST NOT modify the index, config, or store. The web upload path shares the identity namespace but does not print the advisory in this version.

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

### Requirement: One identity namespace for ingested manuals

The ingested `manual_id` SHALL be derived from the manual's filename by one shared helper, sanitized to `[0-9A-Za-z_]` with a deterministic fallback, and MUST be identical for every entrypoint (the ingest CLI, the reindex CLI, and the web upload). The filesystem path MUST NOT carry the identity; staged uploads keep their UUID name. The same PDF MUST always land in the same identity namespace so golden sets, calibration records, and citations agree.

#### Scenario: Two uploads get two identities

- GIVEN two owner uploads with different filenames
- WHEN each is ingested
- THEN each is recorded under its own filename-derived id

#### Scenario: The filename never reaches the staged path

- GIVEN an upload whose name contains path traversal
- WHEN it is staged
- THEN the staged path stays inside the staging directory under a UUID name
- AND the traversal string appears only in the sanitized id

### Requirement: Page figures are extracted as side evidence

When a manual is ingested, each page that embeds a raster image SHALL have its largest embedded image extracted and saved as a PNG at the deterministic path `<FIGURES_DIR>/<revision>/<manual_id>/page-<N>.png`, and that page's child points SHALL carry a `has_figures` payload flag. Extraction is PyMuPDF-only: NO vision model, no network, and no new dependency (ADR-002 budget untouched). A blank `FIGURES_DIR` SHALL disable extraction and leave every flag False. Extraction failure (an undecodable image, an unwritable disk) MUST NOT fail the ingest — the revision still builds, validates and swaps, and the affected page simply has no saved figure. Vector-only diagrams (no raster) are out of scope and stay text-only. The figure is verification-aid data, never part of the answer contract.

#### Scenario: An image page is saved and flagged

- GIVEN a manual page with an embedded raster image
- WHEN it is ingested with a configured `FIGURES_DIR`
- THEN a PNG exists at the deterministic path
- AND that page's child points carry `has_figures=True`

#### Scenario: A text-only page pays nothing

- GIVEN a page with no raster image
- WHEN it is ingested
- THEN no figure file is written and its flag is False

#### Scenario: Extraction disabled

- GIVEN `FIGURES_DIR=""`
- WHEN a manual is ingested
- THEN no figure file is written and every `has_figures` flag is False

#### Scenario: A figure failure never fails the ingest

- GIVEN figure extraction raises for a page
- WHEN the ingest runs
- THEN the revision is still built, count-validated and aliased
