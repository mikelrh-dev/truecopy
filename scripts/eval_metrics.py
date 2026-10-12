#!/usr/bin/env python3
"""Ranking-metrics harness for the offline eval track (Phase 1, slices 1-2).

Runs the four ``scripts/eval.py`` checks' RETRIEVAL half again as numbers:
every hand-verified golden records the RANK of its first relevant page (slice
1: Hit Rate@K + MRR) and the labeled set adds binary page-level P@K/R@K
(slice 2). Nothing here changes a pass/fail check, and nothing here touches
the serving corpus: this is read-only measurement, the seam Phase 2 needs
before it is allowed to move the answering path.

Offline by construction: no LLM call (retrieval is deterministic), no writes
to the corpus. The only live dependency is Qdrant, and an unreachable Qdrant
is an ERROR (exit 1), never a silent pass.

Exit codes:
    0 - the measured metrics were printed (and, with ``--record``, recorded).
    1 - no Qdrant, a malformed labeled set, or a regression against
        ``--baseline``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` importable when this
# file runs directly (``python scripts/eval_metrics.py``) from a fresh clone.
REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (REPO_ROOT / "src", REPO_ROOT):
    if Path(_entry).is_dir() and str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from qdrant_client import QdrantClient  # noqa: E402

from plant_rag import config  # noqa: E402
from plant_rag.answer.pipeline import retrieve_and_rank  # noqa: E402
from plant_rag.eval_metrics import (  # noqa: E402
    RankedQuery,
    build_report,
    load_labeled_set,
)
from plant_rag.retrieve.store import (  # noqa: E402
    TEXT_ALIAS,
    current_target,
)

# A DIFFERENT file from the gate's ``eval/baseline.json`` on purpose: this one
# holds the bare metrics report, the gate's holds ``{metrics, checks}``. One
# shared path would let either ``--record`` overwrite the other's schema, and
# a baseline with the wrong keys compares as "no baseline" = green.
DEFAULT_LABELED_SET = REPO_ROOT / "eval" / "labeled-qa.json"
DEFAULT_BASELINE = REPO_ROOT / "eval" / "metrics-baseline.json"


def current_manual_id(client) -> str | None:
    """The ``manual_id`` the current ``current_text`` alias serves."""
    target = current_target(client, TEXT_ALIAS)
    if target is None:
        return None
    points, _ = client.scroll(
        collection_name=target,
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not points or not points[0].payload:
        return None
    return str(points[0].payload.get("manual_id", "")) or None


def measure_rows(
    client, rows: tuple[RankedQuery, ...]
) -> tuple[RankedQuery, ...]:
    """Re-rank every labeled query and record the page list an answer could cite.

    The recorded field is the deduplicated page sequence in RANKED order — the
    exact window the citation contract draws from — so the metrics are computed
    over what an operator could actually be shown, not over a raw hit list.
    """
    measured: list[RankedQuery] = []
    for row in rows:
        hits = retrieve_and_rank(client, row.query)
        measured.append(
            replace(row, ranked=tuple(dict.fromkeys(hit.page for hit in hits)))
        )
    return tuple(measured)


def rows_for_manual(
    rows: tuple[RankedQuery, ...], manual_id: str | None
) -> tuple[RankedQuery, ...]:
    """Only the cases labeled for ``manual_id`` (the loaded manual's set)."""
    if manual_id is None:
        return rows
    return tuple(row for row in rows if not row.manual_id or row.manual_id == manual_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline ranking metrics (slices 1-2).")
    parser.add_argument("--labeled-set", default=str(DEFAULT_LABELED_SET))
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument(
        "--baseline",
        default=str(DEFAULT_BASELINE),
        help="Baseline JSON to write with --record (and read with --check).",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Write the measured metrics as the baseline (frozen measurement).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare against the baseline and exit 1 on a regression.",
    )
    parser.add_argument(
        "--manual-id",
        default=None,
        help="Override the loaded manual id (default: read from the corpus).",
    )
    args = parser.parse_args(argv)

    if args.record and args.check:
        print("ERROR: --record y --check son mutuamente excluyentes", file=sys.stderr)
        return 2

    try:
        rows, metadata = load_labeled_set(args.labeled_set)
    except (OSError, ValueError) as exc:
        print(f"ERROR: set etiquetado inválido: {exc}", file=sys.stderr)
        return 1

    try:
        client = QdrantClient(url=config.QDRANT_URL, timeout=10)
        client.get_collections()
    except Exception as exc:
        print(f"ERROR: no se puede alcanzar Qdrant: {exc}", file=sys.stderr)
        return 1

    loaded = args.manual_id or current_manual_id(client) or ""
    selected = rows_for_manual(rows, loaded or None)
    if not selected:
        print(
            f"ERROR: el set etiquetado no tiene casos para el manual {loaded!r}",
            file=sys.stderr,
        )
        return 1

    print("Plant RAG — métricas de recuperación (offline)")
    print(f"Qdrant: {config.QDRANT_URL}")
    print(f"Manual cargado: {loaded or 'desconocido'}")
    print(f"K: {args.k} (TOP_K del contrato de respuesta)")
    print(f"Set etiquetado: {args.labeled_set} ({len(selected)} casos)")
    if metadata.get("criteria"):
        print(f"Criterio de relevancia: {metadata['criteria']}")
    if metadata.get("status") == "seeded" and metadata.get("target_size"):
        print(
            f"NOTA: set semilla ({len(rows)} de {metadata['target_size']} casos "
            "objetivo) — faltan anotaciones hechas contra el corpus vivo."
        )
    print("-" * 60)

    measured = measure_rows(client, selected)
    report = build_report(measured, k=args.k)
    print(report.format_es())
    print("-" * 60)
    print("JSON:", json.dumps(report.to_dict(), ensure_ascii=False))

    if args.record:
        target = Path(args.baseline)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline grabada en {target}")
        return 0

    if args.check:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_file():
            print(
                f"ERROR: no hay baseline en {baseline_path}; genérela con "
                "--record contra el corpus vivo",
                file=sys.stderr,
            )
            return 1
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        from scripts.regression_gate import compare_metrics

        regressions = compare_metrics(baseline, report.to_dict())
        if regressions:
            print("REGRESIÓN:", file=sys.stderr)
            for line in regressions:
                print(f"  - {line}", file=sys.stderr)
            return 1
        print("sin regresiones respecto a la baseline")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
