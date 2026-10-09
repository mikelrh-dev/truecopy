#!/usr/bin/env python3
"""Reindex CLI — replace the current manual with a new revision.

Blue/green flow (ADR-001): builds ``r{N+1}`` from the replacement PDF,
validates point counts, swaps both ``current_*`` aliases atomically in one
call, and deletes ``r{N}``. An interrupted build leaves ``r{N}`` serving
untouched — queries never mix old and new chunks (manual-ingestion spec:
"Reindex interrupted").

This is the operator's tool for manual revision replacement. It is the same
pipeline as ``scripts/ingest.py`` under the hood; the difference is semantic:
``ingest.py`` is first-load oriented, ``reindex.py`` is replacement-oriented
and documents the rollback contract explicitly.

Usage:
    python scripts/reindex.py /ruta/al/manual-nuevo.pdf

Exit codes:
    0 — replacement succeeded; ``r{N+1}`` serves, ``r{N}`` deleted.
    1 — fatal error (bad args, missing PDF, empty corpus, Qdrant/Ollama down).
        On any failure the previous revision keeps serving untouched.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` importable when this
# file runs directly (``python scripts/reindex.py``) from a fresh clone.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from plant_rag import config
from plant_rag.ingest.reindex import run_reindex
from plant_rag.retrieve.embedder import get_embedder
from qdrant_client import QdrantClient


def _manual_id_from_path(pdf_path: Path) -> str:
    """Derive a stable manual identifier from the filename (no extension)."""
    return pdf_path.stem.replace(" ", "_").replace("-", "_")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replace the current manual with a new revision (blue/green)."
    )
    parser.add_argument(
        "pdf",
        type=Path,
        help="Replacement manual PDF (Siemens 828D or updated version).",
    )
    parser.add_argument(
        "--manual-id",
        type=str,
        default=None,
        help="Manual identifier carried in every payload (default: PDF stem).",
    )
    args = parser.parse_args(argv)

    pdf_path = args.pdf
    if not pdf_path.is_file():
        print(f"ERROR: replacement PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    manual_id = args.manual_id or _manual_id_from_path(pdf_path)

    # Show what's currently serving before replacing it.
    try:
        client = QdrantClient(url=config.QDRANT_URL, timeout=30)
        from plant_rag.retrieve.store import current_revision

        previous = current_revision(client)
    except Exception as exc:
        print(f"ERROR: cannot reach Qdrant to read current revision: {exc}", file=sys.stderr)
        return 1

    print(f"Current revision before replacement: r{previous} (if any)")
    print(f"Replacement PDF: {pdf_path}")
    print(f"Manual id: {manual_id}")
    print(f"Qdrant: {config.QDRANT_URL}")
    print(f"Model:  {config.OLLAMA_MODEL} (respuesta), BGE-M3 fp32 (embedding)")
    print(f"Batches: {config.BATCH_PAGES} páginas")

    try:
        client = QdrantClient(url=config.QDRANT_URL, timeout=120)
        encode = get_embedder().encode
        result = run_reindex(
            pdf_path,
            client=client,
            encode=encode,
            manual_id=manual_id,
            batch_pages=config.BATCH_PAGES,
        )
    except Exception as exc:
        print(f"ERROR: replacement failed — previous revision keeps serving: {exc}", file=sys.stderr)
        return 1

    print(f"OK — revisión {result.revision} en servicio (reemplazó a r{previous or 'ninguna'}):")
    print(f"  text_points  = {result.text_points}")
    print(f"  alarm_points = {result.alarm_points}")
    if result.previous_revision:
        print(f"  previous     = {result.previous_revision}")
    if result.stale_collections:
        print(f"  stale (no borraron): {', '.join(result.stale_collections)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
