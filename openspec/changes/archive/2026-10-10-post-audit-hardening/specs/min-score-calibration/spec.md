# Delta for min-score-calibration

## MODIFIED Requirements

### Requirement: Verbatim identifier keys

Records SHALL key on the ingested id verbatim. There is ONE identity namespace —
the sanitized filename — shared by the CLI entrypoints and the web upload, so a
manual ingested from the web and the same manual ingested from the CLI key on the
same id (the previous split where the web used `manual-actual` is removed). A
miss MUST fail clean to "no record" and MUST NOT fuzzy-match to a similar key.

#### Scenario: Key miss

- GIVEN no record exists for the requested id
- WHEN a suggestion is requested
- THEN "no record" is reported for that id only

#### Scenario: Web and CLI share the key

- GIVEN a manual ingested from the web under its filename-derived id
- WHEN the CLI ingests the same manual
- THEN both resolve to the same id and the same calibration record
