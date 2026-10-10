"""Bounded PDF page loading for manual ingestion.

Conventions established here (apply to the ``ingest`` package):

* Ingestion is streaming: :func:`iter_batches` is a generator that holds only
  the current batch of extracted text in memory. That is what bounds peak RSS
  inside the 16GB budget (ADR-002) regardless of manual length, and it
  satisfies the manual-ingestion spec scenario "Corpus does not fit one batch".
* Pages are self-contained frozen value objects: text AND table grids are
  extracted eagerly while the document is open, so a batch never depends on
  the underlying PyMuPDF handle after it is yielded (no lifetime traps).
* Every page carries its SECTION title from the PDF outline when the manual
  has one. A chunk that says "Winter Tires" beats a chunk that says only
  "235/40R19 42 psi" for the question "presión de neumáticos en invierno":
  retrieval matches on the topic, not just the sentence.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import fitz  # PyMuPDF, pinned at 1.24.14 (ADR-002).

from plant_rag import config

# Raw cell grid from PyMuPDF ``find_tables()``: rows of cell strings (or None).
TableGrid = list[list[str | None]]

# One PDF outline entry: [level, title, 1-based page].
TocEntry = Sequence[object]


@dataclass(frozen=True)
class Page:
    """One extracted PDF page: provenance, text, and table cell grids.

    ``tables`` holds the rows returned by PyMuPDF ``find_tables()`` for this
    page (empty when the page has no detectable table), so alarm extraction
    can run anywhere — long after the document was closed. ``section`` is the
    deepest outline title covering this page, or ``None`` for manuals without
    an outline.
    """

    number: int
    text: str
    tables: tuple[TableGrid, ...] = ()
    section: str | None = None
    #: True when the page embeds at least one raster image. The flag alone is
    #: captured here (cheap, no bytes): the bytes are extracted later, one page
    #: at a time, by ``ingest.figures`` — so a batch never carries image data
    #: and the ADR-002 RSS bound is untouched. Text-only pages stay False and
    #: pay nothing.
    has_figures: bool = False

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"page number must be >= 1, got {self.number}")


def _section_titles(toc: Sequence[TocEntry]) -> dict[int, str]:
    """Map each page number to the deepest outline title covering it.

    Outline entries arrive in document order as ``[level, title, page]``; an
    entry owns the pages from its own page until the next entry's page. When
    several entries share a page (a section starting mid-page), the deepest
    one wins — it is the most specific description of that content.
    """
    titles: dict[int, str] = {}
    ranks: dict[int, int] = {}
    for index, entry in enumerate(toc):
        level, title, start = int(entry[0]), str(entry[1]).strip(), int(entry[2])
        if not title:
            continue
        stop = int(toc[index + 1][2]) - 1 if index + 1 < len(toc) else start
        for page in range(max(start, 1), max(stop, start) + 1):
            if level >= ranks.get(page, -1):
                titles[page] = title
                ranks[page] = level
    return titles


def iter_batches(
    pdf_path: str | Path, batch_pages: int = config.BATCH_PAGES
) -> Iterator[list[Page]]:
    """Yield sequential page batches of at most ``batch_pages`` pages.

    Pages are extracted lazily in document order, so at any moment only the
    current batch's text is resident — the RSS bound required by the
    manual-ingestion spec ("Batched ingestion with provenance"). Text,
    ``find_tables()`` grids, and outline section titles are all captured
    while the document is open.

    Args:
        pdf_path: Path to the manual PDF.
        batch_pages: Maximum pages per batch; defaults to ``config.BATCH_PAGES``
            (100, ADR-002).

    Yields:
        Lists of :class:`Page` in ascending page order, each with at most
        ``batch_pages`` entries (the final batch may be shorter).

    Raises:
        ValueError: if ``batch_pages`` is less than 1.
        FileNotFoundError: if ``pdf_path`` does not exist.
    """
    if batch_pages < 1:
        raise ValueError(f"batch_pages must be >= 1, got {batch_pages}")
    path = Path(pdf_path)
    if not path.is_file():
        raise FileNotFoundError(f"manual PDF not found: {path}")

    with fitz.open(path) as doc:
        sections = _section_titles(doc.get_toc())
        batch: list[Page] = []
        for index in range(doc.page_count):
            handle = doc.load_page(index)
            batch.append(
                Page(
                    number=index + 1,
                    text=handle.get_text("text"),
                    tables=tuple(
                        table.extract() for table in handle.find_tables().tables
                    ),
                    section=sections.get(index + 1),
                    has_figures=bool(handle.get_images(full=False)),
                )
            )
            if len(batch) >= batch_pages:
                yield batch
                batch = []
        if batch:
            yield batch
