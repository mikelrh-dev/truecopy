"""Blue/green reindex: build ``r{N+1}``, validate, atomic alias swap, drop ``r{N}`` (ADR-001).

Failure contract (manual-ingestion spec: "Reindex interrupted"): aliases are
touched only AFTER the new revision validates, and the swap of ``current_text``
+ ``current_alarms`` happens in a single ``update_collection_aliases`` call.
An interrupted build therefore leaves the previous revision serving untouched,
and a completed swap leaves exactly one corpus behind — queries can never mix
old and new chunks.

The physical contract shared with the query path — collection naming, alias
names, revision discovery, and collection creation (vectors on disk) — lives
in :mod:`plant_rag.retrieve.store`; this module owns the build/validate/swap
pipeline itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    CreateAlias,
    CreateAliasOperation,
    DeleteAlias,
    DeleteAliasOperation,
    PointStruct,
)

from plant_rag import config
from plant_rag.ingest.alarms import AlarmRow, extract_alarms
from plant_rag.ingest.chunker import ChildChunk, ParentChunk, chunk_page
from plant_rag.ingest.loader import iter_batches
from plant_rag.retrieve.store import (
    ALARMS_ALIAS,
    TEXT_ALIAS,
    alarm_collection,
    create_alarm_collection,
    create_text_collection,
    current_revision,
    list_revisions,
    text_collection,
)

# Encodes child texts to embedding vectors; injected so ingestion never
# imports the model stack (BGE-M3 lands in Phase 3, tests inject fakes).
EncodeFn = Callable[[Sequence[str]], Sequence[Sequence[float]]]

_NAMESPACE = uuid.NAMESPACE_URL


@dataclass(frozen=True)
class ReindexResult:
    """Outcome of a completed reindex: which revision now serves traffic."""

    revision: str
    previous_revision: str | None
    text_points: int
    alarm_points: int
    stale_collections: tuple[str, ...]  # old collections that resisted deletion

    def __post_init__(self) -> None:
        if not self.revision.strip():
            raise ValueError("revision must not be empty")
        if self.text_points < 1:
            raise ValueError(f"text_points must be >= 1, got {self.text_points}")
        if self.alarm_points < 0:
            raise ValueError(f"alarm_points must be >= 0, got {self.alarm_points}")


def next_revision(client: QdrantClient) -> int:
    """Fresh revision number that can never collide with existing data."""
    candidates = list_revisions(client)
    current = current_revision(client)
    if current is not None:
        candidates.add(current)
    return max(candidates, default=0) + 1


def swap_aliases(client: QdrantClient, revision: int) -> None:
    """Point both ``current_*`` aliases at ``revision`` in ONE atomic call.

    Qdrant applies all alias operations of a single request atomically, so
    readers observe either the old pair or the new pair — never a mix.
    """
    aliases = {a.alias_name: a.collection_name for a in client.get_aliases().aliases}
    operations = []
    for alias_name, target in (
        (TEXT_ALIAS, text_collection(revision)),
        (ALARMS_ALIAS, alarm_collection(revision)),
    ):
        if alias_name in aliases:
            operations.append(
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias_name))
            )
        operations.append(
            CreateAliasOperation(
                create_alias=CreateAlias(
                    alias_name=alias_name, collection_name=target
                )
            )
        )
    client.update_collection_aliases(change_aliases_operations=operations)


def validate_revision(
    client: QdrantClient, revision: int, expected_text: int, expected_alarms: int
) -> None:
    """Prove the built revision is complete before any alias moves (ADR-001)."""
    for name, expected in (
        (text_collection(revision), expected_text),
        (alarm_collection(revision), expected_alarms),
    ):
        info = client.get_collection(name)
        if info.points_count != expected:
            raise ValueError(
                f"{name} holds {info.points_count} points, expected {expected}"
            )


def _discard(client: QdrantClient, *names: str) -> None:
    """Best-effort cleanup of incomplete revision collections.

    Failures are swallowed: leftovers are unaliased (never retrievable) and
    ``next_revision`` skips their numbers, while the original error must be
    the one the caller sees.
    """
    for name in names:
        try:
            client.delete_collection(name)
        except Exception:  # noqa: BLE001 - cleanup must not mask the real error
            pass


def _point_id(key: str) -> str:
    """Deterministic point id so re-runs of one revision never duplicate."""
    return str(uuid.uuid5(_NAMESPACE, f"plant-rag:{key}"))


def run_reindex(
    pdf_path: str | Path,
    *,
    client: QdrantClient,
    encode: EncodeFn,
    manual_id: str,
    batch_pages: int = config.BATCH_PAGES,
) -> ReindexResult:
    """Run a full reindex of ``pdf_path`` as the next revision (ADR-001).

    Streams pages in bounded batches, encodes children with the injected
    ``encode``, upserts into fresh ``manual_r{N+1}_{text,alarms}`` collections,
    validates point counts, and only then swaps both ``current_*`` aliases in
    one call and deletes the previous revision.

    Args:
        pdf_path: Manual PDF to (re)index.
        client: Qdrant client (real or test double).
        encode: Maps child texts to embedding vectors of ``config.EMBED_DIMS``.
        manual_id: Manual identifier carried in every payload.
        batch_pages: Loader batch bound (RSS budget, ADR-002).

    Returns:
        :class:`ReindexResult` for the revision now serving traffic.

    Raises:
        ValueError: on blank ``manual_id``, an empty corpus, an ``encode``
            contract violation, or incomplete collections — in every case the
            aliases still serve the previous revision.
        Exception: whatever the pipeline raised; the half-built ``r{N+1}``
            collections are removed and ``r{N}`` keeps serving.
    """
    if not manual_id.strip():
        raise ValueError("manual_id must not be empty")

    revision = next_revision(client)
    revision_tag = f"r{revision}"
    text_name = text_collection(revision)
    alarm_name = alarm_collection(revision)
    previous = current_revision(client)
    text_points = 0
    alarm_points = 0

    try:
        # Collections exist before any data: an interrupted run can never be
        # mistaken for a complete revision, and counts gate the swap. Their
        # shapes live in retrieve.store: on-disk vectors (ADR-002) for text,
        # payload-only + alarm_code index (ADR-003) for alarms.
        create_text_collection(client, text_name)
        create_alarm_collection(client, alarm_name)

        for batch in iter_batches(pdf_path, batch_pages):
            pairs: list[tuple[ParentChunk, ChildChunk]] = []
            alarm_rows: list[tuple[int, AlarmRow]] = []
            for page in batch:
                parent, page_children = chunk_page(
                    page, manual_id=manual_id, revision=revision_tag
                )
                if parent is not None:
                    pairs.extend((parent, child) for child in page_children)
                for row_index, row in enumerate(
                    extract_alarms(page, manual_id=manual_id, revision=revision_tag)
                ):
                    alarm_rows.append((row_index, row))

            children = [child for _, child in pairs]
            vectors = list(encode([child.text for child in children])) if children else []
            if len(vectors) != len(children):
                raise ValueError(
                    f"encode returned {len(vectors)} vectors for "
                    f"{len(children)} children"
                )
            for vector in vectors:
                if len(vector) != config.EMBED_DIMS:
                    raise ValueError(
                        f"encode returned {len(vector)} dims, "
                        f"expected {config.EMBED_DIMS}"
                    )

            if children:
                client.upsert(
                    text_name,
                    [
                        PointStruct(
                            id=_point_id(child.child_id),
                            vector=[float(value) for value in vector],
                            payload={
                                "manual_id": child.manual_id,
                                "page": child.page,
                                "revision": child.revision,
                                "child_id": child.child_id,
                                "parent_id": child.parent_id,
                                "child_text": child.text,
                                "parent_text": parent.text,
                            },
                        )
                        for (parent, child), vector in zip(pairs, vectors)
                    ],
                    wait=True,
                )
                text_points += len(children)
            if alarm_rows:
                client.upsert(
                    alarm_name,
                    [
                        PointStruct(
                            id=_point_id(
                                f"{manual_id}:{revision_tag}:alarm:"
                                f"{row.page}:{row_index}:{row.alarm_code}"
                            ),
                            vector={},
                            payload={
                                "manual_id": row.manual_id,
                                "page": row.page,
                                "revision": row.revision,
                                "alarm_code": row.alarm_code,
                                "text": row.text,
                            },
                        )
                        for row_index, row in alarm_rows
                    ],
                    wait=True,
                )
                alarm_points += len(alarm_rows)

        if text_points == 0:
            raise ValueError(f"no indexable text found in {pdf_path}")
        validate_revision(client, revision, text_points, alarm_points)
        swap_aliases(client, revision)
    except BaseException:
        _discard(client, text_name, alarm_name)
        raise

    # Swap is done: the old revision is no longer reachable through any alias.
    stale: list[str] = []
    if previous is not None and previous != revision:
        for name in (text_collection(previous), alarm_collection(previous)):
            try:
                client.delete_collection(name)
            except Exception:  # noqa: BLE001 - already unreachable, keep serving
                stale.append(name)

    return ReindexResult(
        revision=revision_tag,
        previous_revision=f"r{previous}" if previous is not None else None,
        text_points=text_points,
        alarm_points=alarm_points,
        stale_collections=tuple(stale),
    )
