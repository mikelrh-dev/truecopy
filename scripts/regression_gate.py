#!/usr/bin/env python3
"""Regression gate for the release checks (Phase 1, idea 4).

The gate answers one question: did retrieval get WORSE? It runs the four
``scripts/eval.py`` checks plus the slice-1/slice-2 ranking metrics and
compares them against a frozen baseline. Any check that flips to failing, any
metric that drops, and any golden whose rank worsens is a REGRESSION and exits
1. A gate with no baseline also exits 1 — a gate that passes by default is
decoration.

Why the comparison lives in pure functions (``compare_metrics`` /
``compare_checks``): the interesting behavior is the comparison, and pinning it
hermetically is what lets CI trust the gate without a live stack. The full run
still needs Qdrant; ``--metrics-only`` is the Docker-free half, which is
exactly the half the synthetic-regression test drives.

Exit codes:
    0 - everything at or above the baseline.
    1 - a regression, a missing baseline, or an unreachable stack.
    2 - malformed arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` and ``scripts.*``
# importable when this file runs directly from a fresh clone.
REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = REPO_ROOT / "src"
for _entry in (str(_SRC), str(REPO_ROOT)):
    if Path(_entry).is_dir() and _entry not in sys.path:
        sys.path.insert(0, _entry)

from qdrant_client import QdrantClient  # noqa: E402

from plant_rag import config  # noqa: E402
from plant_rag.eval_metrics import build_report, load_labeled_set  # noqa: E402

DEFAULT_BASELINE = REPO_ROOT / "eval" / "baseline.json"
DEFAULT_LABELED_SET = REPO_ROOT / "eval" / "labeled-qa.json"

# Metrics that must never go DOWN. Direction matters: a floor that is lowered
# raises recall and lowers precision, and the gate must see the loss.
_LOWER_IS_WORSE = ("hit_rate", "mrr", "precision", "recall")


def compare_metrics(
    baseline: dict, current: dict, *, tolerance: float = 0.0
) -> list[str]:
    """Every metric/rank regression between two measured reports.

    Args:
        baseline: A previously recorded report (``MetricsReport.to_dict()``).
        current: The freshly measured report, same shape.
        tolerance: Allowed drop that does not count as a regression. Zero by
            default: with a deterministic corpus there is no noise to forgive,
            and a silent tolerance is how a real regression hides.

    Returns:
        One Spanish line per regression, empty when nothing regressed.
    """
    regressions: list[str] = []
    for key in _LOWER_IS_WORSE:
        was = baseline.get(key)
        now = current.get(key)
        if was is None or now is None:
            continue
        if now < float(was) - tolerance:
            regressions.append(f"{key}: {was} -> {now}")

    was_refusals = baseline.get("refusal_ok")
    now_refusals = current.get("refusal_ok")
    if (
        was_refusals is not None
        and now_refusals is not None
        and now_refusals < was_refusals
    ):
        regressions.append(
            f"controles fuera de corpus: {was_refusals} -> {now_refusals} "
            "(el pool dejó de estar vacío)"
        )

    baseline_ranks = baseline.get("ranks") or {}
    current_ranks = current.get("ranks") or {}
    for query, was in baseline_ranks.items():
        if query not in current_ranks:
            regressions.append(f"{query!r}: ya no se mide")
            continue
        now = current_ranks[query]
        if was is not None and now is None:
            regressions.append(f"{query!r}: rango {was} -> fuera del top-K")
        elif was is not None and now is not None and int(now) > int(was):
            regressions.append(f"{query!r}: rango {was} -> {now}")
    return regressions


def compare_checks(baseline: dict, current: dict) -> list[str]:
    """Every check that flipped to failing since the baseline."""
    regressions: list[str] = []
    for name, was in (baseline.get("checks") or {}).items():
        now = (current.get("checks") or {}).get(name)
        if was and not now:
            regressions.append(f"check {name!r}: pasaba y ahora falla")
    return regressions


def run_live_checks(client, golden_set) -> dict[str, bool]:
    """The four ``scripts/eval.py`` mandatory checks, as pass/fail flags."""
    from scripts import eval as eval_cli

    results = (
        eval_cli.check_alarm_known_and_unknown(client, golden_set),
        eval_cli.check_crosslingual_es_en(client),
        eval_cli.check_refusal_out_of_corpus(client),
        eval_cli.check_golden_grounding(client, golden_set),
    )
    return {result.name: bool(result.passed) for result in results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Release regression gate.")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--labeled-set", default=str(DEFAULT_LABELED_SET))
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument(
        "--record",
        action="store_true",
        help="Write the current measurement as the baseline and exit 0.",
    )
    parser.add_argument(
        "--metrics-only",
        action="store_true",
        help=(
            "Skip the four live eval checks (no corpus needed): the metrics "
            "half, which is what runs without Docker."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="Allowed metric drop that is not a regression (default 0.0).",
    )
    args = parser.parse_args(argv)

    try:
        client = QdrantClient(url=config.QDRANT_URL, timeout=10)
        client.get_collections()
    except Exception as exc:
        print(f"ERROR: cannot reach Qdrant: {exc}", file=sys.stderr)
        return 1

    from scripts import eval as eval_cli
    from scripts.eval_metrics import measure_rows, rows_for_manual

    rows, metadata = load_labeled_set(args.labeled_set)
    loaded = eval_cli.current_manual_id(client) or ""
    selected = rows_for_manual(rows, loaded or None)
    if not selected:
        print(
            f"ERROR: el set etiquetado no tiene casos para el manual {loaded!r}",
            file=sys.stderr,
        )
        return 1

    report = build_report(measure_rows(client, selected), k=args.k)
    checks = (
        {}
        if args.metrics_only
        else run_live_checks(client, eval_cli.golden_set_for(loaded))
    )
    current: dict[str, object] = {"metrics": report.to_dict(), "checks": checks}
    # A mandatory check that is red on HEAD is a failure in its own right: it
    # must never be baselined away (that is how a gate turns green while the
    # system is broken). It also blocks --record, so a red HEAD cannot become
    # the reference every later PR is excused against.
    failing = sorted(name for name, ok in checks.items() if not ok)

    print("Plant RAG — regression gate")
    print(f"Manual cargado: {loaded or 'desconocido'} (revisión desde el alias)")
    print(f"Set etiquetado: {args.labeled_set}; criterio: {metadata.get('criteria', '')[:80]}...")
    print(f"Modo: {'solo métricas' if args.metrics_only else 'completo (4 checks + métricas)'}")
    print("-" * 60)
    print(report.format_es())
    for name, ok in checks.items():
        print(f"check {name}: {'OK' if ok else 'ROJO'}")

    baseline_path = Path(args.baseline)
    if failing:
        print(
            f"ERROR: checks obligatorios en rojo en HEAD ({', '.join(failing)}): "
            "una puerta no puede pasar con un check obligatorio fallando, y no "
            "se graba baseline sobre un HEAD rojo",
            file=sys.stderr,
        )
        return 1

    if args.record:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"baseline grabada en {baseline_path}")
        return 0

    if not baseline_path.is_file():
        print(
            f"ERROR: no hay baseline en {baseline_path}; genérela con --record "
            "contra el corpus vivo (una puerta sin baseline no puede pasar)",
            file=sys.stderr,
        )
        return 1

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    # A baseline missing a block compares as "nothing to check" and passes,
    # which is exactly the false green a gate exists to prevent. Fail instead.
    baseline_metrics = baseline.get("metrics")
    if not isinstance(baseline_metrics, dict) or not baseline_metrics:
        print(
            f"ERROR: la baseline {baseline_path} no trae bloque 'metrics' "
            "(¿la escribió scripts/eval_metrics.py en su lugar?). "
            "Regenérela con --record",
            file=sys.stderr,
        )
        return 1
    if not args.metrics_only and not isinstance(baseline.get("checks"), dict):
        print(
            f"ERROR: la baseline {baseline_path} no trae bloque 'checks'; "
            "regenérela con --record en modo completo",
            file=sys.stderr,
        )
        return 1

    regressions = compare_metrics(
        baseline_metrics, report.to_dict(), tolerance=args.tolerance
    )
    if not args.metrics_only:
        regressions += compare_checks(baseline, current)

    print("-" * 60)
    if regressions:
        print(f"REGRESIONES ({len(regressions)}):", file=sys.stderr)
        for line in regressions:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print("GATE OK: sin regresiones respecto a la baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
