"""Exact alarm-code fast path (retrieval spec: A2 / ADR-003).

Conventions established here:

* A query is code-shaped only when it is exactly 4-6 digits — the same shape
  ingestion accepts as an alarm table's first cell, so the fast path can never
  chase a code the corpus could not have indexed. Surrounding whitespace is
  trimmed; anything else (words, punctuation, other lengths) is NOT code-shaped.
* Resolution is an exact ``MatchValue`` on ``alarm_code`` inside the
  ``current_alarms`` collection, additionally scoped to the revision that alias
  names. Embeddings are never involved: a payload-only collection has no
  vectors to search (ADR-003).
* :func:`lookup` returns three distinguishable outcomes so the caller never
  guesses: ``None`` = not code-shaped (route to semantic search), ``()``
  = code-shaped but unknown (refusal path, NO semantic fallback, NO LLM), a
  non-empty tuple = the matching alarm entry with its page number.
* :func:`lookup_code` shares the same resolution but skips the whole-question
  shape gate: the answering pipeline uses it to union alarm rows for codes
  mentioned inside natural-language questions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from plant_rag.retrieve.store import ALARMS_ALIAS, current_target, revision_tag

# Identical to the ingestion rule (ingest.alarms): code shape is one contract.
_ALARM_CODE_RE = re.compile(r"^\d{4,6}$")

# One code can legitimately appear on several pages; this bounds the scan.
_MAX_ALARM_PAGES = 10


@dataclass(frozen=True)
class AlarmHit:
    """One alarm row with full provenance for the citation contract."""

    manual_id: str
    page: int
    revision: str
    alarm_code: str
    text: str

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")
        if not _ALARM_CODE_RE.fullmatch(self.alarm_code):
            raise ValueError(
                f"alarm_code must be 4-6 digits, got {self.alarm_code!r}"
            )


def alarm_code(query: str) -> str | None:
    """The code when ``query`` is exactly code-shaped, else ``None``.

    Whitespace-only or blank input is simply not code-shaped (``None``), so
    the caller keeps its routing total: code -> exact path, anything else ->
    semantic search.
    """
    stripped = query.strip()
    return stripped if _ALARM_CODE_RE.fullmatch(stripped) else None


def _payload_field(payload: dict, key: str, point_id) -> object:
    """Read one payload key, failing actionably if ingestion lost it."""
    try:
        return payload[key]
    except KeyError as exc:
        raise ValueError(f"point {point_id} payload is missing {key!r}") from exc


def lookup_code(client: QdrantClient, code: str) -> tuple[AlarmHit, ...]:
    """Resolve an already code-shaped alarm code (retrieval spec: A2).

    The union entry point for questions that MENTION a code ("¿qué significa
    la alarma 1192?"): the pipeline extracts the token, and the row it finds
    becomes prompt evidence instead of staying hidden behind the fast path's
    whole-question shape gate. An unknown code yields ``()`` (no evidence,
    not a refusal - refusal belongs to a fully code-shaped question).

    Args:
        client: Qdrant client (real or test double).
        code: Digits only, exactly 4-6 long (the ingestion shape rule).

    Returns:
        Every matching alarm entry in the current revision, or ``()``.

    Raises:
        ValueError: if ``code`` is not code-shaped or a payload is missing
            provenance - a shape mismatch here would be a caller bug.
    """
    if not _ALARM_CODE_RE.fullmatch(code):
        raise ValueError(f"code must be 4-6 digits, got {code!r}")
    return _resolve(client, code)


def lookup(client: QdrantClient, query: str) -> tuple[AlarmHit, ...] | None:
    """Resolve an alarm-code query exactly (retrieval spec: A2).

    Args:
        client: Qdrant client (real or test double).
        query: Raw operator query.

    Returns:
        ``None`` when the query is not code-shaped - the caller routes it to
        semantic search. ``()`` when the code is unknown in the current
        revision (feeds the exact refusal, never a semantic fallback). A
        non-empty tuple holds every matching alarm entry, page numbers first.

    Raises:
        ValueError: if a hit payload is missing provenance.
    """
    code = alarm_code(query)
    if code is None:
        return None
    return _resolve(client, code)


def _resolve(client: QdrantClient, code: str) -> tuple[AlarmHit, ...]:
    """Exact MatchValue resolution for one code, scoped to the alias revision."""
    target = current_target(client, ALARMS_ALIAS)
    if target is None:
        return ()  # nothing ingested: an unknown code, by definition
    revision = revision_tag(target)
    if revision is None:
        return ()  # alias points outside our contract: never serve it

    alarm_filter = Filter(
        must=[
            FieldCondition(key="alarm_code", match=MatchValue(value=code)),
            FieldCondition(key="revision", match=MatchValue(value=revision)),
        ]
    )
    hits: list[AlarmHit] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=target,
            scroll_filter=alarm_filter,
            limit=_MAX_ALARM_PAGES,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            hits.append(
                AlarmHit(
                    manual_id=str(_payload_field(payload, "manual_id", point.id)),
                    page=int(_payload_field(payload, "page", point.id)),
                    revision=str(_payload_field(payload, "revision", point.id)),
                    alarm_code=str(
                        _payload_field(payload, "alarm_code", point.id)
                    ),
                    text=str(_payload_field(payload, "text", point.id)),
                )
            )
        if offset is None or len(hits) >= _MAX_ALARM_PAGES:
            return tuple(hits)
