# answering delta — Cited page figure is a verification aid

### Requirement: Cited page figure is a verification aid

When an answer cites a page whose ingested payload carried `has_figures` AND whose saved figure file exists, the operator UI SHALL show that figure beside the page's text excerpt. A missing file, a disabled `FIGURES_DIR`, or a page without the flag MUST degrade silently to the text excerpt alone. The figure MUST NEVER gate, delay, or alter the answer, its text, or its citations — it is a verification aid, not evidence, and never enters the prompt.

#### Scenario: Figure shown beside the excerpt

- GIVEN a cited page flagged `has_figures` with a saved file on disk
- WHEN the answer is rendered
- THEN the figure is shown inside that citation's excerpt

#### Scenario: Missing file fails open

- GIVEN the flag is set but the file is absent
- WHEN the answer is rendered
- THEN only the text excerpt is shown

#### Scenario: Answer contract untouched

- GIVEN any figure outcome
- WHEN the answer is rendered
- THEN the answer text and citations are unchanged
