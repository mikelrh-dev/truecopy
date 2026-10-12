# Eval metrics track (parked)

Status: parked for maintainer review. No implementation approved.

## Context

Two goals, one track: production signal **and** ML/IR study. The maintainer is
an ML/AI student, so implementation effort doubles as practice. Every slice
below runs fully offline — nothing here may touch the serving path.

## Safety rule (non-negotiable)

- Read-only inputs: Qdrant (read), goldens, query JSONL, calibration records.
- Outputs go to `outputs/` only.
- No changes to `src/plant_rag/answer/pipeline.py`, retrieval, or serving.
  Eval traffic already passes no cache and no log; metrics code keeps it
  that way.

## What exists today (baseline)

- `scripts/eval.py`: 4 checks (alarm retrieval, cross-lingual ES→EN, refusal
  out-of-corpus, golden grounding) + `MIN_SCORE` sweep that informs without
  applying.
- Query JSONL: `latency_s` + per-phase timings (`embed/qdrant/rerank/llm_s`),
  feedback events, `decline-strong-pool` signals.
- Structural enforcement instead of scoring: citation validator (no valid
  citation → no answer), page diversity, reranker.

## Slices (in order)

1. **Hit Rate@K + MRR on goldens.** Expected pages already hand-verified;
   record their rank, not just pass/fail. Teaches ranking evaluation.
   Afternoon-sized.
2. **P@K/R@K on a hand-labeled set.** 20–30 queries with written relevance
   criteria. The value is the annotation lesson (disagreement, bias), not
   the numbers. Pays off fully at multi-manual scale.
3. **Faithfulness by NLI sampling.** Local entailment model: does the cited
   extract support the answer text? Covers the documented residual hole
   ("valid citation, overstated text") without a hot-path judge.
4. **Answer relevancy by cosine (to break it).** Question vs. answer
   embeddings. Cheap to build; instructive where it fails (true but
   non-answering replies score high). Kept as study material, not a gate.

## Explicitly deferred

- Hot-path LLM judges (cost, complexity, against the local-only philosophy).
- Context-relevance scoring per query (pipeline already enforces it;
  decline signals + operator votes carry the signal for free).

## Open questions for review

- Golden format: where does expected-page rank live (extend record or
  separate metrics output)?
- NLI model choice for slice 3 (local, CPU-budgeted, consistent with
  ADR-002)?
- Slice 1 acceptance: exact rank semantics under backfill (variant pools
  only fill uncovered slots)?
