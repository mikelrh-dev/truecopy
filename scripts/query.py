#!/usr/bin/env python3
"""Query CLI — one-shot question through retrieval + answering contract.

Reuses the same generation wiring as the web layer (alarm fast path, then
semantic search, then the LLM under the ADR-004 contract) so owner spot-checks
see the same behavior the operators get in the UI.

Usage:
    python scripts/query.py "¿cuál es la presión de servicio?"
    python scripts/query.py "1234"            # alarm-code fast path
    python scripts/query.py --json "¿qué es el bypass?"

Exit codes:
    0 — answer produced (grounded or refusal).
    1 — fatal error (missing services, bad args, transport failure).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` importable when this
# file runs directly (``python scripts/query.py``) from a fresh clone.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from plant_rag import config
from plant_rag.models import Answer
from plant_rag.scheduling.scheduler import Scheduler
from plant_rag.answer.pipeline import answer_question
from plant_rag.answer.llm import LLMError
from plant_rag.monitoring import QueryLog


@dataclass
class CliAnswer:
    kind: str
    text_es: str
    citations: list[dict[str, object]]


def _answer_to_cli(answer: Answer) -> CliAnswer:
    return CliAnswer(
        kind=answer.kind,
        text_es=answer.text_es,
        citations=[{"manual_id": c.manual_id, "page": c.page} for c in answer.citations],
    )


def _generate(question: str, remaining_s: float) -> Answer:
    """The shared chain (answer.pipeline): alarm fast path -> hybrid -> rerank -> LLM."""
    return answer_question(question, remaining_s, log=QueryLog.from_config())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-shot operator question through retrieval + answering."
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=None,
        help="Operator question in Spanish (quoted).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the answer as JSON instead of readable text.",
    )
    args = parser.parse_args(argv)

    if not args.question:
        parser.print_help()
        return 1

    print(f"Pregunta: {args.question}")
    print(f"Qdrant: {config.QDRANT_URL} | Modelo: {config.OLLAMA_MODEL}")
    print("-" * 60)

    try:
        scheduler = Scheduler(_generate)
        position = scheduler.submit(args.question)
        print(f"Posición en la cola: {position}")
        print("-" * 60)

        answer: Answer | None = None
        while True:
            next_answer = scheduler.run_once()
            if next_answer is None:
                break
            answer = next_answer

        if answer is None:
            print("ERROR: no answer produced.", file=sys.stderr)
            return 1

        cli = _answer_to_cli(answer)

        if args.json:
            print(json.dumps(asdict(cli), ensure_ascii=False, indent=2))
        else:
            print(f"Tipo: {cli.kind}")
            print(f"Respuesta: {cli.text_es}")
            if cli.citations:
                print("Fuentes:")
                for c in cli.citations:
                    print(f"  [Fuente: {c['manual_id']} Página {c['page']}]")
    except LLMError as exc:
        print(f"ERROR: servicio no disponible — {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
