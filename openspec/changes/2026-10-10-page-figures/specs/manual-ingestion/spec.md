# manual-ingestion delta — Page figures are extracted as side evidence

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
