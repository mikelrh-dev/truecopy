"""Qdrant data access: aliases, revision scoping, collection contract, payload mapping.

Conventions established here (apply package-wide):

* store is the single place that knows the physical side of Qdrant: collection
  naming (``manual_r{N}_{text,alarms}``), creation (vectors ``on_disk=True`` per
  ADR-002's memory line; alarms payload-only per ADR-003), the ``current_*``
  aliases, and revision discovery. The write path (``ingest.reindex``) imports
  these so ingestion and querying can never drift apart.
* Every read resolves through a ``current_*`` alias AND filters on the
  revision that alias names (retrieval spec: "Current-revision scope"). The
  filter is defense in depth behind the single-current-alias invariant: even a
  stale or stray point left inside the current collection can never surface.
* Payload-to-``Hit`` mapping happens exactly once, here. ``Hit.child`` carries
  the matched ``child_text`` and ``Hit.parent`` carries the page-sized
  ``parent_text`` — the English passage Phase 4 grounds on and cites by page.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    TextIndexParams,
    TextIndexType,
    TokenizerType,
    VectorParams,
)

from plant_rag import config
from plant_rag.models import Hit

# The one current corpus is exposed through these two aliases; every query
# resolves through them, which is what makes replacement atomic (ADR-001).
TEXT_ALIAS = "current_text"
ALARMS_ALIAS = "current_alarms"

_COLLECTION_RE = re.compile(r"^manual_r(\d+)_(text|alarms)$")


def text_collection(revision: int) -> str:
    """Physical collection holding child vectors for revision ``revision``."""
    return f"manual_r{revision}_text"


def alarm_collection(revision: int) -> str:
    """Physical payload-only collection holding alarm rows for ``revision``."""
    return f"manual_r{revision}_alarms"


def revision_tag(collection_name: str) -> str | None:
    """``"manual_r2_text"`` -> ``"r2"``; ``None`` for anything else."""
    match = _COLLECTION_RE.match(collection_name)
    return f"r{match.group(1)}" if match else None


def list_revisions(client: QdrantClient) -> set[int]:
    """Revision numbers of every physical collection currently present."""
    revisions: set[int] = set()
    for collection in client.get_collections().collections:
        match = _COLLECTION_RE.match(collection.name)
        if match:
            revisions.add(int(match.group(1)))
    return revisions


def current_target(client: QdrantClient, alias: str) -> str | None:
    """Physical collection behind ``alias``, or ``None`` before first ingestion."""
    aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
    return aliases.get(alias)


def current_revision(client: QdrantClient) -> int | None:
    """Revision the ``current_text`` alias serves, or ``None`` on first run."""
    target = current_target(client, TEXT_ALIAS)
    if target is None:
        return None
    match = _COLLECTION_RE.match(target)
    return int(match.group(1)) if match else None


def text_index_params() -> TextIndexParams:
    """Full-text index shape for ``child_text`` — ONE contract, both sides.

    ``hybrid.hybrid_search`` matches the question's tokens against this index
    via ``MatchText``; keeping the tokenizer parameters here means the writer
    (ingestion) and the reader (retrieval) can never drift apart.
    """
    return TextIndexParams(
        type=TextIndexType.TEXT,
        tokenizer=TokenizerType.WORD,
        min_token_len=2,
        max_token_len=20,
        lowercase=True,
        on_disk=False,
    )


def create_text_index(client: QdrantClient, name: str) -> None:
    """Create the ``child_text`` full-text index (hybrid retrieval's text branch).

    Without it, ``MatchText`` finds nothing and hybrid silently degrades to
    dense-only on that revision — which is exactly how the live r6 corpus was
    found before the index existed on every revision by construction.
    """
    client.create_payload_index(
        name,
        field_name="child_text",
        field_schema=text_index_params(),
        wait=True,
    )


def create_text_collection(client: QdrantClient, name: str) -> None:
    """Create the child-vector collection for one revision with vectors on disk.

    ``on_disk=True`` keeps vectors out of the resident memory line (ADR-002);
    COSINE matches the similarity ranking the query path relies on. The
    ``child_text`` full-text index is created here too, so every revision that
    ingestion builds is hybrid-searchable from birth (see :func:`create_text_index`).
    """
    client.create_collection(
        name,
        VectorParams(
            size=config.EMBED_DIMS, distance=Distance.COSINE, on_disk=True
        ),
    )
    create_text_index(client, name)


def create_alarm_collection(client: QdrantClient, name: str) -> None:
    """Create the payload-only alarm collection with its ``alarm_code`` index.

    Alarm answers are exact ``MatchValue`` lookups (ADR-003), never vector
    search, so alarm rows carry no embedding at all and the index they do need
    is a keyword index on the code.
    """
    client.create_collection(name, vectors_config={})
    client.create_payload_index(
        name,
        field_name="alarm_code",
        field_schema=PayloadSchemaType.KEYWORD,
        wait=True,
    )


def _payload_field(payload: dict, key: str, point_id) -> object:
    """Read one payload key, failing actionably if ingestion lost it."""
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"point {point_id} payload is missing {key!r}") from exc


def revision_filter(revision: str) -> Filter:
    """Match only chunks tagged with ``revision`` (spec: current-revision scope)."""
    return Filter(
        must=[FieldCondition(key="revision", match=MatchValue(value=revision))]
    )


def hit_from_payload(payload: dict, point_id) -> Hit:
    """Map one text-collection payload onto a :class:`Hit`.

    The single payload-to-``Hit`` mapping site, shared by the dense search
    and the hybrid search so a missing provenance field fails the same way
    (``ValueError``) no matter which branch retrieved the chunk.
    """
    return Hit(
        manual_id=str(_payload_field(payload, "manual_id", point_id)),
        page=int(_payload_field(payload, "page", point_id)),
        revision=str(_payload_field(payload, "revision", point_id)),
        child=str(_payload_field(payload, "child_text", point_id)),
        parent=str(_payload_field(payload, "parent_text", point_id)),
    )


def search(
    client: QdrantClient,
    query_vector: Sequence[float],
    *,
    top_k: int = config.TOP_K,
) -> tuple[Hit, ...]:
    """Top-k children for ``query_vector`` from the current revision only.

    Resolves ``current_text``, scopes the search to the revision that alias
    names, and maps payloads to :class:`Hit` with the page number the answer
    contract cites (retrieval spec: "Cross-lingual ES→EN retrieval").

    Args:
        client: Qdrant client (real or test double).
        query_vector: BGE-M3 vector of exactly ``config.EMBED_DIMS`` dims.
        top_k: Children to return (``config.TOP_K`` by default).

    Returns:
        Matching hits, best first, with every score below
        ``config.MIN_SCORE`` dropped — a weak match must behave exactly like
        no match so the answer gate refuses on empty without an LLM call
        (retrieval spec: "Query outside the corpus"; ADR-004). Callers
        refuse on empty, never synthesize.

    Raises:
        ValueError: if the query vector has the wrong dimensionality or a
            hit payload is missing provenance — a silent partial hit would
            break the citation contract downstream.
    """
    if len(query_vector) != config.EMBED_DIMS:
        raise ValueError(
            f"query vector has {len(query_vector)} dims, "
            f"expected {config.EMBED_DIMS}"
        )
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")

    target = current_target(client, TEXT_ALIAS)
    if target is None:
        return ()
    revision = revision_tag(target)
    if revision is None:
        return ()  # alias points outside our contract: never serve it

    response = client.query_points(
        collection_name=target,
        query=[float(value) for value in query_vector],
        limit=top_k,
        query_filter=revision_filter(revision),
        with_payload=True,
        with_vectors=False,
    )
    hits: list[Hit] = []
    for point in response.points:
        if point.score < config.MIN_SCORE:
            # Relevance floor (config.MIN_SCORE, calibrated 0.40): below it
            # the passage is not evidence, and non-evidence must reach the
            # refusal gate instead of the LLM (ADR-004 "never invent").
            continue
        hits.append(hit_from_payload(point.payload or {}, point.id))
    return tuple(hits)
