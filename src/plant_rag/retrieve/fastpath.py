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
* :func:`has_alarm_table` answers the one question the refusal contract needs
  before it may refuse by SHAPE: does this corpus have an alarm table at all?
  Where it does, an unknown code is the exact refusal (ADR-003, unchanged);
  where it does not, the digits are prose and the caller routes them to
  semantic search instead of failing a valid question (E3).
* The scan cap (``_MAX_ALARM_PAGES``) is bounded, never SILENT: when the server
  reports more rows than the cap (a live ``offset`` is the server saying there
  IS more), the truncation is emitted as one ``logging`` warning. A code with
  exactly the cap and nothing after it emits nothing, so the signal never
  cries wolf. Warning is the whole effect - the returned rows are unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from plant_rag.retrieve.store import (
    ALARMS_ALIAS,
    current_target,
    revision_filter,
    revision_tag,
)

# Identical to the ingestion rule (ingest.alarms): code shape is one contract.
_ALARM_CODE_RE = re.compile(r"^\d{4,6}$")

# One code can legitimately appear on several pages; this bounds the scan.
_MAX_ALARM_PAGES = 10

# Stdlib logging, module-scoped: the truncation signal is observable without a
# new dependency, an injected seam, or any change to what this module returns.
logger = logging.getLogger(__name__)


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


def has_alarm_table(client: QdrantClient) -> bool:
    """Whether this corpus HAS an indexed alarm table under this revision.

    The capability probe behind the E3 gate (retrieval spec: "Per-corpus
    alarm fastpath gating"). True only when ``current_alarms`` resolves AND
    holds at least one row in the current revision: an empty (or stale)
    collection is, for routing purposes, no table at all - a code-shaped
    question on such a corpus is prose, not a code.

    Resolved at QUERY time rather than from configuration on purpose: a config
    knob describing the index is a second copy of the truth and drifts from it
    on the first reindex nobody updated. The cost is one bounded scroll, paid
    only by the unknown-code branch of the answering gate.

    Uses a revision-scoped ``scroll(limit=1)`` rather than ``count()``: the
    same call shape ``_resolve`` already uses, so every existing client and
    test double models it.
    """
    target = current_target(client, ALARMS_ALIAS)
    if target is None:
        return False  # nothing ingested: no table, by definition
    revision = revision_tag(target)
    if revision is None:
        return False  # alias outside our contract: never serve it
    points, _ = client.scroll(
        collection_name=target,
        scroll_filter=revision_filter(revision),
        limit=1,
        with_payload=False,
        with_vectors=False,
    )
    return bool(points)


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
        if offset is None:
            return tuple(hits)
        if len(hits) >= _MAX_ALARM_PAGES:
            # E5: the cut used to be invisible. A live ``offset`` means the
            # server has more rows for this code, so this IS a truncation -
            # exactly the case worth recording, and the only one that can be
            # claimed from the returned page alone. One line, no extra query.
            logger.warning(
                "alarm code %s spans more pages than the %d-page scan cap: "
                "returning the first %d rows only",
                code,
                _MAX_ALARM_PAGES,
                len(hits),
            )
            return tuple(hits)
