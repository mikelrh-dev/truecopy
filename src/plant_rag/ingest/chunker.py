"""Parent-child chunking with full provenance (manual-ingestion spec).

Conventions established here:

* The parent chunk is one page's text: citations are page-level
  (``[Fuente: … Página N]``), so parent <-> page must map 1:1 and every child
  can resolve its citation through ``page`` alone.
* Child chunks are overlapping character windows over the parent — small
  enough for top-k retrieval, while the parent text rides along in the point
  payload for answer generation (ADR-004 feeds parents to the LLM).
* Children carry their page's section title as a prefix; parents never do.
  The prefix exists for RETRIEVAL only (embedding, text index, reranker
  pairs): it gives a window its topic without touching the verbatim evidence
  the answer contract shows the model.
* Every chunk is a frozen value object carrying manual_id, page, parent
  linkage, and revision (spec: "every child chunk MUST retain manual
  identifier, page number, and parent linkage").
"""

from __future__ import annotations

from dataclasses import dataclass

from plant_rag.ingest.loader import Page

# BGE-M3 handles far more, but ~1000-char children keep top-k diverse while
# the parent supplies full context (ADR-002 CPU budget: fewer, shorter vectors).
CHILD_MAX_CHARS = 1000
CHILD_OVERLAP_CHARS = 150


@dataclass(frozen=True)
class ParentChunk:
    """Full text of one page — the unit handed to the LLM for grounding."""

    manual_id: str
    page: int
    revision: str
    parent_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")
        if not self.parent_id.strip():
            raise ValueError("parent_id must not be empty")


@dataclass(frozen=True)
class ChildChunk:
    """One retrieval unit: a window over its parent with full provenance."""

    manual_id: str
    page: int
    revision: str
    parent_id: str
    child_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")
        if not self.parent_id.strip():
            raise ValueError("parent_id must not be empty")
        if not self.child_id.strip():
            raise ValueError("child_id must not be empty")


def _split_windows(text: str, max_chars: int, overlap: int) -> list[str]:
    """Split ``text`` into windows of at most ``max_chars`` sharing ``overlap``.

    Cuts prefer the last whitespace inside the window so words stay whole;
    overlap keeps context continuous across window boundaries.
    """
    if len(text) <= max_chars:
        return [text]
    windows: list[str] = []
    start = 0
    while True:
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind(" "), window.rfind("\n"))
            if cut > max_chars // 2:
                end = start + cut
        windows.append(text[start:end])
        if end >= len(text):
            return windows
        start = max(end - overlap, start + 1)


def chunk_page(
    page: Page, *, manual_id: str, revision: str
) -> tuple[ParentChunk | None, list[ChildChunk]]:
    """Split one page into its parent chunk and overlapping child windows.

    Pages with no extractable text produce no chunks (nothing to embed,
    nothing to retrieve); every other page yields exactly one parent and one
    or more children.

    Returns:
        ``(parent, children)`` — ``(None, [])`` for a blank page.

    Raises:
        ValueError: if ``manual_id`` or ``revision`` is blank.
    """
    if not manual_id.strip():
        raise ValueError("manual_id must not be empty")
    if not revision.strip():
        raise ValueError("revision must not be empty")

    text = page.text.strip()
    if not text:
        return None, []

    parent_id = f"{manual_id}:{revision}:p{page.number}"
    parent = ParentChunk(
        manual_id=manual_id,
        page=page.number,
        revision=revision,
        parent_id=parent_id,
        text=text,
    )
    section = (page.section or "").strip()
    prefix = f"[{section}] " if section else ""
    children = [
        ChildChunk(
            manual_id=manual_id,
            page=page.number,
            revision=revision,
            parent_id=parent_id,
            child_id=f"{parent_id}:c{index}",
            text=f"{prefix}{window}",
        )
        for index, window in enumerate(
            _split_windows(text, CHILD_MAX_CHARS, CHILD_OVERLAP_CHARS)
        )
    ]
    return parent, children
