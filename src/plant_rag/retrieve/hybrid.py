"""Hybrid retrieval: dense cosine + full-text, fused with RRF (retrieval spec).

Conventions established here:

* ONE ``query_points`` call with two prefetches and ``Fusion.RRF`` fusion, so
  the fused ranking happens inside Qdrant (no client-side merge, no second
  network round-trip): the dense branch keeps the calibrated relevance floor
  and the text branch gates tokens, so both precision mechanisms of the dense
  path survive the fusion unchanged.
* Revision scoping mirrors ``store.search`` exactly: resolve ``current_text``,
  take the revision tag from the physical collection, and filter BOTH
  prefetches on it. An alias outside the ``manual_r{N}_text`` contract returns
  ``()`` instead of serving stray data.
* The relevance floor is applied per-prefetch BEFORE fusion (Qdrant applies
  ``score_threshold`` to each prefetch's own scores), so a Spanish question
  with no English token overlap yields an empty fused list, which the caller
  must turn into the exact refusal - never an empty prompt (ADR-004).
* The text branch needs the payload index on ``child_text`` (case-insensitive
  word tokenizer, min token length 2). ``store.create_text_collection``
  creates it for every revision at ingest time (``store.text_index_params()``
  is the single definition of its shape), so a missing index on a revision
  built by an older pipeline is the ONLY way this degrades to dense-only.
* Without that index ``MatchText`` returns zero points (verified live: an
  untokenized match never degenerates into "match everything"), so a missing
  index degrades to dense-only retrieval instead of failing the query.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchText,
    MatchValue,
    Prefetch,
)

from plant_rag import config
from plant_rag.models import Hit
from plant_rag.retrieve.store import (
    TEXT_ALIAS,
    current_target,
    hit_from_payload,
    revision_filter,
    revision_tag,
)

# Candidates fetched per branch before fusion: deeper than TOP_K so the
# reranker reorders a real pool instead of re-cutting the fused head.
_PREFETCH_LIMIT = 20


def hybrid_search(
    client: QdrantClient,
    query_vector: list[float],
    question: str,
    *,
    top_k: int = config.RERANK_POOL,
) -> tuple[Hit, ...]:
    """Fuse dense and full-text retrieval into one RRF-ranked candidate pool.

    Args:
        client: Open Qdrant client (real or test double); no implicit
            connection setup lives here.
        query_vector: BGE-M3 vector of exactly ``config.EMBED_DIMS`` dims.
        question: Raw operator query; its tokens are matched against the
            English ``child_text`` passages (case-insensitive word index).
        top_k: Fused candidates to return (``config.RERANK_POOL``: the pool
            the reranker cuts down to ``config.TOP_K``).

    Returns:
        Fused candidates in RRF rank order, mapped to :class:`Hit`. ``()``
        when the alias is absent/uncontracted or no branch produced an
        above-floor candidate - the caller refuses on empty.

    Raises:
        ValueError: if the vector has the wrong dimensionality, the question
            or vector is empty, or ``top_k < 1`` - silent mis-scoped searches
            would break the citation contract downstream.
    """
    if len(query_vector) != config.EMBED_DIMS:
        raise ValueError(
            f"query vector has {len(query_vector)} dims, "
            f"expected {config.EMBED_DIMS}"
        )
    if not question.strip():
        raise ValueError("question must not be empty")
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")

    target = current_target(client, TEXT_ALIAS)
    if target is None:
        return ()
    revision = revision_tag(target)
    if revision is None:
        return ()  # alias points outside our contract: never serve it

    # Branch 1: dense with the calibrated floor (config.MIN_SCORE), applied
    # per-prefetch before fusion: below-floor passages never reach RRF.
    dense = Prefetch(
        query=[float(value) for value in query_vector],
        filter=revision_filter(revision),
        score_threshold=config.MIN_SCORE,
        limit=_PREFETCH_LIMIT,
    )
    # Branch 2: full-text AND-match on child_text, same revision scope.
    text = Prefetch(
        filter=Filter(
            must=[
                FieldCondition(key="child_text", match=MatchText(text=question)),
                FieldCondition(key="revision", match=MatchValue(value=revision)),
            ]
        ),
        limit=_PREFETCH_LIMIT,
    )

    response = client.query_points(
        collection_name=target,
        prefetch=[dense, text],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
        with_vectors=False,
        timeout=10,
    )
    return tuple(
        hit_from_payload(point.payload or {}, point.id) for point in response.points
    )
