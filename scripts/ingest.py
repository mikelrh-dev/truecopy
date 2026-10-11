#!/usr/bin/env python3
"""Ingestion CLI — load a manual PDF and build the next revision.

Thin wrapper over ``plant_rag.ingest.loader``, ``chunker``, ``alarms``, and
``reindex`` — no duplicated ingestion logic. Every tunable that matters is
env-overridable via ``PLANT_RAG_*`` (config.py); nothing is hardcoded.

Usage:
    python scripts/ingest.py /ruta/al/manual.pdf

What it does (ADR-001 blue/green):
    1. Streams pages in 100-page batches (RSS budget, ADR-002).
    2. Builds ``manual_r{N+1}_text`` + ``manual_r{N+1}_alarms``.
    3. Encodes children with BGE-M3 on CPU (lazy load, ~2.5GB peak).
    4. Upserts points, validates counts, swaps both ``current_*`` aliases
       atomically, deletes the previous revision.
    5. On failure: aliases untouched, previous revision keeps serving.
    6. On success: prints a READ-ONLY calibration advisory for this
       manual/revision when ``config.CALIBRATION_PATH`` carries one
       (``plant_rag.calibration``) — a suggestion, never a config change.

Exit codes:
    0 — ingestion succeeded.
    1 — fatal error (bad args, missing PDF, empty corpus, Qdrant/Ollama down).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` importable when this
# file runs directly (``python scripts/ingest.py``) from a fresh clone.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from plant_rag import config
from plant_rag.calibration import advisory
from plant_rag.ingest.reindex import manual_id_from_filename, run_reindex
from plant_rag.retrieve.embedder import get_embedder
from qdrant_client import QdrantClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a manual PDF and build the next revision (blue/green)."
    )
    parser.add_argument(
        "pdf",
        type=Path,
        help="Manual PDF to ingest (Siemens 828D or replacement).",
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
        print(f"ERROR: manual PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    manual_id = args.manual_id or manual_id_from_filename(pdf_path.name)

    print(f"Ingesting {pdf_path} as manual '{manual_id}'...")
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
        print(f"ERROR: ingestion failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK — revisión {result.revision} en servicio:")
    print(f"  text_points  = {result.text_points}")
    print(f"  alarm_points = {result.alarm_points}")
    if result.previous_revision:
        print(f"  previous     = {result.previous_revision}")
    if result.stale_collections:
        print(f"  stale (no borraron): {', '.join(result.stale_collections)}")

    # Read-only calibration advisory for THIS manual/revision: eval informs,
    # the owner applies. It never mutates the index, config, or the store;
    # an empty CALIBRATION_PATH prints nothing at all.
    note = advisory(
        config.CALIBRATION_PATH,
        manual_id,
        result.revision,
        cache_path=config.ANSWER_CACHE_PATH,
    )
    if note is not None:
        print(note)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
