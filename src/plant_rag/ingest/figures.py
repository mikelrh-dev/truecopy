"""Page-figure extraction for ingestion (phase-1 ``[IMAGEN]`` handling).

Design (heuristic, no vision model):

* Only pages whose loader pass flagged ``has_figures`` are touched; a text-only
  page costs nothing.
* For a flagged page the LARGEST embedded raster image is extracted and saved
  as a PNG. If the image cannot be decoded (masks, unusual colour spaces) the
  page is rendered whole instead, so the operator still sees the diagram in
  context. Vector-only diagrams (no raster) are out of scope for v1 — PyMuPDF
  ``get_images`` does not see them — and stay text-only.
* The destination is deterministic: ``<dir>/<revision>/<manual_id>/page-<N>.png``.
  The web derives the same path and shows the figure only when the file exists,
  so a missing file degrades to the text excerpt and never breaks the answer.
* Extraction is PyMuPDF-only (already pinned, ADR-002): no model, no download,
  no VRAM. A failure to extract ONE page is reported by omission, never by an
  exception — a figure is a trust aid, not part of the answer contract.
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF, pinned at 1.24.14 (ADR-002).

# Rendered fallback resolution: readable diagram, small file (ADR-002 disk).
_FALLBACK_DPI = 110

_UNSAFE = re.compile(r"[^0-9A-Za-z_.-]+")


def _safe_manual_id(manual_id: str) -> str:
    """A filesystem-safe directory name for one manual (never a path escape)."""
    cleaned = _UNSAFE.sub("_", manual_id).strip("_")
    return cleaned or "manual-sin-nombre"


def figure_path(
    figures_dir: str | Path, revision: str, manual_id: str, page: int
) -> Path:
    """The deterministic PNG path for one page's figure."""
    return Path(figures_dir) / revision / _safe_manual_id(manual_id) / f"page-{page}.png"


def extract_figure_png(pdf_path: str | Path, page_number: int) -> bytes | None:
    """The largest figure on ``page_number`` as PNG bytes, or ``None``.

    Tries the largest embedded image first; if it cannot be decoded, renders
    the whole page. A page without raster images returns ``None``.
    """
    with fitz.open(pdf_path) as doc:
        if not 1 <= page_number <= doc.page_count:
            return None
        page = doc.load_page(page_number - 1)
        images = page.get_images(full=True)
        if not images:
            return None
        # info = (xref, smask, width, height, bpc, colorspace, ...)
        largest = max(images, key=lambda info: int(info[2]) * int(info[3]))
        xref = int(largest[0])
        try:
            pixmap = fitz.Pixmap(doc, xref)
            if pixmap.n - pixmap.alpha < 3:  # grayscale/1-bit: normalise to RGB
                pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
            return pixmap.tobytes("png")
        except Exception:  # noqa: BLE001 - fall back to the rendered page
            try:
                return page.get_pixmap(dpi=_FALLBACK_DPI).tobytes("png")
            except Exception:  # noqa: BLE001 - a figure is best-effort, never fatal
                return None


def save_page_figures(
    pdf_path: str | Path,
    pages: tuple[int, ...] | list[int],
    *,
    figures_dir: str | Path,
    revision: str,
    manual_id: str,
) -> dict[int, Path]:
    """Extract and save the figure of every flagged page; return page -> path.

    A blank ``figures_dir`` disables extraction (returns ``{}``). One page's
    failure never stops the others.
    """
    if not str(figures_dir).strip() or not pages:
        return {}
    saved: dict[int, Path] = {}
    for page_number in pages:
        data = extract_figure_png(pdf_path, page_number)
        if data is None:
            continue
        destination = figure_path(figures_dir, revision, manual_id, page_number)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        except OSError:  # noqa: BLE001 - a disk failure must not fail the ingest
            continue
        saved[page_number] = destination
    return saved
