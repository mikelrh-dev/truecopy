# Delta for retrieval

## ADDED Requirements (proposed — not yet applied to the living spec)

### Requirement: Per-corpus alarm fastpath gating

A corpus WITHOUT an indexed alarm table MUST NOT refuse code-shaped questions
outright. When `current_alarms` is absent (or holds no rows under the current
revision), a 4–6-digit query routes to semantic search like any other
question. Where an alarm table EXISTS, unknown codes still refuse exactly
with no semantic fallback (ADR-003 unchanged for that case).

Rationale: on an alarm-less enterprise manual, `"2024"` or `"404"` is far
more likely a year, a page, or an error reference in prose than an alarm code;
a guaranteed refusal there is a recall failure, not contract discipline. The
eval already models this per manual (`expect_alarms=False`,
`scripts/eval.py:283-297`) — the runtime never got the same switch.

#### Scenario: Digits on an alarm-less corpus retrieve semantically

- GIVEN a corpus with no alarm table under the current revision
- WHEN the operator asks exactly `"404"`
- THEN the question is searched semantically (dense + text, floor applies)
- AND the answer is grounded or refused on the evidence, never refused by shape

#### Scenario: Unknown code still refuses where a table exists

- GIVEN a corpus WITH an indexed alarm table
- WHEN the operator asks an unknown code (e.g. `"000000"`)
- THEN the system returns the exact refusal with no semantic fallback

#### Scenario: Known code still answers directly

- GIVEN a corpus WITH an indexed alarm table containing the code
- WHEN the operator asks that code
- THEN the direct cited alarm answer is returned with zero embedding and zero LLM traffic

### Requirement: Text-branch recall stays gated behind dense evidence

Until measured otherwise during recalibration, the pool-level dense probe
(`retrieve/hybrid.py:124-140`) REMAINS the single admission rule for
text-assisted recall: a pool whose dense branch has no above-floor candidate
is empty even on a perfect token match. The full-text branch reorders within
fused results; it never independently admits evidence.

Rationale: relaxing this without measurement would let token-only
coincidences reach the prompt. Revisit ONLY with sweep evidence showing
rescued in-corpus queries and zero new out-of-corpus admissions.

#### Scenario: Text-only match without dense support refuses

- GIVEN a question whose tokens match `child_text` but whose dense score is
  below floor on every candidate
- WHEN the pool is built
- THEN the pool is empty and the caller refuses exactly
