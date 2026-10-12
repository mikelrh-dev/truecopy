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
* The fallback is LOUD (E4/M6): an unloadable model or a scoring failure emits
  one ``logging`` warning with its cause, because an operator reading unranked
  results is running downgraded with no way to know. Disabled by CONFIGURATION
  is not a fallback and stays silent - that would warn on every query of a
  deliberately rerank-free deployment. No branch here ever raises a new
  exception or changes the returned order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from plant_rag import config
from plant_rag.models import Hit

# Stdlib logging, module-scoped: observability with no new dependency and no
# signature change (same mechanism as the fastpath scan-cap signal).
logger = logging.getLogger(__name__)

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
        except Exception as exc:  # noqa: BLE001 - ranking degrades, never availability
            # A missing/broken model degrades ranking; it must not degrade
            # silently, or unranked results look like ranked ones forever.
            logger.warning(
                "rerank model %r could not be loaded (%s): keeping retrieval "
                "order for %d candidates",
                config.RERANK_MODEL,
                exc,
                len(hits),
            )
            return tuple(hits)[:top_k]

    pairs = [(question, hit.child) for hit in hits]
    try:
        raw_scores = list(active_scorer(pairs))
    except Exception as exc:  # noqa: BLE001 - ranking degrades, never availability
        # Same contract as an unavailable model: keep the incoming order, and
        # say so once (one warning per answer, not one per candidate).
        logger.warning(
            "rerank scoring failed (%s): keeping retrieval order for %d candidates",
            exc,
            len(hits),
        )
        return tuple(hits)[:top_k]

    if len(raw_scores) != len(hits):
        raise ValueError(
            f"scorer returned {len(raw_scores)} scores for {len(hits)} candidates"
        )

    scores = [float(value) for value in raw_scores]
    order = sorted(range(len(hits)), key=lambda index: (-scores[index], index))
    return tuple(hits[index] for index in order[:top_k])


def warmup(*, scorer: Scorer | None = None) -> bool:
    """Load the cross-encoder weights before the first operator question.

    The reranker is ~2GB and its load lands on whoever asks first, so the web
    app calls this when it opens. It scores one throwaway pair rather than
    calling :func:`rerank`, because ``rerank`` degrades to the incoming order
    when the model fails — which would report a false success here. Empty
    ``config.RERANK_MODEL`` (reranking disabled) warms nothing.

    Returns:
        ``True`` when a scoring call ran, ``False`` when reranking is disabled
        or the model could not be loaded. Never raises.
    """
    if scorer is None and not config.RERANK_MODEL:
        return False
    active = scorer
    if active is None:
        try:
            active = _default_scorer()
        except Exception:  # noqa: BLE001 - warm-up is opportunistic
            return False
    try:
        active([("calentamiento", "calentamiento")])
    except Exception:  # noqa: BLE001 - availability over precision, as rerank
        return False
    return True
