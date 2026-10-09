"""CPU cross-encoder reranking over the retrieval pool (retrieval spec: rerank).

Conventions established here:

* Reranking is a RANKING optimization, never a correctness gate. It only
  reorders and truncates an already-validated candidate pool, so the evidence
  that reached it stays evidence; if the model cannot be loaded or scoring
  fails, the pool keeps its incoming order instead of losing answers
  (availability over precision for a single-machine operator console).
* The cross-encoder runs on CPU explicitly: the stack is local-only and
  ADR-002's budget has no GPU to spare. The model is loaded lazily on the
  first scored call and cached for the process, so a refusal path (empty
  pool) never pays for a 2GB weights load.
* Scoring pairs the operator question against each candidate's ``child``
  (the matched chunk), not the page-sized parent: a focused pair is both
  cheaper and more discriminative, and the model truncates anyway.
* The scorer is injectable: unit tests pass a deterministic double and never
  touch the network, the cache, or the weights.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from plant_rag import config
from plant_rag.models import Hit

# A scorer receives (question, passage) pairs and returns one score per pair.
Scorer = Callable[[list[tuple[str, str]]], Sequence[float]]

# Lazily built CrossEncoder, cached for the process lifetime (the weights are
# ~2GB; loading them per query would dominate the 45s budget).
_CACHED_SCORER: Scorer | None = None


def _default_scorer() -> Scorer:
    """Return the process-wide CPU cross-encoder scorer, loading it once."""
    global _CACHED_SCORER
    if _CACHED_SCORER is None:
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(config.RERANK_MODEL, device="cpu")

        def score(pairs: list[tuple[str, str]]) -> Sequence[float]:
            return model.predict(pairs)

        _CACHED_SCORER = score
    return _CACHED_SCORER


def rerank(
    question: str,
    hits: Sequence[Hit],
    *,
    top_k: int = config.TOP_K,
    scorer: Scorer | None = None,
) -> tuple[Hit, ...]:
    """Order ``hits`` by cross-encoder relevance and cut them to ``top_k``.

    Args:
        question: Raw operator query (Spanish).
        hits: Candidate pool from retrieval (dense + text + code union), best
            retrieval order first.
        top_k: How many candidates survive to prompting (``config.TOP_K``).
        scorer: Injected scorer for tests; the default loads
            ``config.RERANK_MODEL`` on CPU. An empty ``config.RERANK_MODEL``
            disables reranking entirely (retrieval order is kept).

    Returns:
        The ``top_k`` best candidates, ties resolved in incoming order (a
        stable sort keeps retrieval order as the deterministic fallback).

    Raises:
        ValueError: if ``top_k < 1``, the question is blank, or the scorer
            returned a score count that does not match the pool — silently
            pairing scores with the wrong passages would corrupt ranking.
    """
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")
    if not question.strip():
        raise ValueError("question must not be empty")
    if not hits:
        # Empty pool short-circuits before any model load: the refusal path
        # must stay free of network/disk work.
        return ()
    if scorer is None and not config.RERANK_MODEL:
        return tuple(hits)[:top_k]  # reranking disabled by configuration

    active_scorer = scorer
    if active_scorer is None:
        try:
            active_scorer = _default_scorer()
        except Exception:
            # A missing/broken model degrades ranking, never availability.
            return tuple(hits)[:top_k]

    pairs = [(question, hit.child) for hit in hits]
    try:
        raw_scores = list(active_scorer(pairs))
    except Exception:
        # Same contract as an unavailable model: keep the incoming order.
        return tuple(hits)[:top_k]

    if len(raw_scores) != len(hits):
        raise ValueError(
            f"scorer returned {len(raw_scores)} scores for {len(hits)} candidates"
        )

    scores = [float(value) for value in raw_scores]
    order = sorted(range(len(hits)), key=lambda index: (-scores[index], index))
    return tuple(hits[index] for index in order[:top_k])
