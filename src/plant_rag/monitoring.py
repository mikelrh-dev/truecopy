"""Persistent operator-query log (JSONL) and its aggregate stats.

Conventions established here:

* ONE JSON object per line, appended under a lock: the LAN web layer writes
  from concurrent sessions, so the log is the one place cross-session state
  is shared besides the scheduler. Append-only, never rewritten.
* The log records FACTS about an answer — question, kind, latency, cited
  pages — never the answer text and never anything an operator typed beyond
  the question itself. It lives on the operator's machine (gitignored path
  by default) because the documentation never leaves it.
* A logging failure must NEVER break an answer: every write is best-effort
  and swallows its own errors. An operator waiting on a machine answer does
  not care that the disk is full.
* ``answer_question`` logs only when a caller passes a ``QueryLog`` — the
  web and CLI entrypoints do; the eval does not, because harness traffic is
  not operator traffic.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from plant_rag import config
from plant_rag.models import Answer

_FIELDS = (
    "ts",
    "question",
    "kind",
    "latency_s",
    "pages",
    "manual_id",
    "revision",
    "model",
)


@dataclass(frozen=True)
class QueryRecord:
    """One answered operator question — the unit the log aggregates on."""

    ts: float
    question: str
    kind: str
    latency_s: float
    pages: tuple[int, ...]
    manual_id: str
    revision: str
    model: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class QueryLog:
    """Append-only JSONL log of operator questions with a read-side stats view."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls) -> QueryLog | None:
        """The configured log, or ``None`` when logging is disabled."""
        if not config.QUERY_LOG_PATH:
            return None
        return cls(config.QUERY_LOG_PATH)

    def record(self, record: QueryRecord) -> None:
        """Append one record; best-effort — a failure never breaks answering."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(record.to_json() + "\n")
        except Exception:  # noqa: BLE001 - the log must never break an answer
            pass

    def records(self) -> list[QueryRecord]:
        """Every parseable record; unreadable lines are skipped, never fatal."""
        if not self.path.is_file():
            return []
        parsed: list[QueryRecord] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if isinstance(data, dict) and data.get("event") == "feedback":
                            # Feedback events are a DIFFERENT schema. Branch on
                            # the event BEFORE any _FIELDS access so a vote line
                            # is excluded by construction, never misread as a
                            # corrupt answer (operator-feedback spec: "Feedback
                            # records MUST NOT count toward answer stats").
                            continue
                        parsed.append(QueryRecord(**{k: data[k] for k in _FIELDS}))
                    except Exception:  # noqa: BLE001 - skip corrupt lines
                        continue
        except Exception:  # noqa: BLE001 - an unreadable log reads as empty
            return []
        return parsed


def build_record(
    question: str, answer: Answer, latency_s: float, *, model: str
) -> QueryRecord:
    """The record for one answer: provenance from its citations, no content."""
    pages = tuple(
        dict.fromkeys(
            citation.page
            for citation in answer.citations
        )
    )
    manual_id = answer.citations[0].manual_id if answer.citations else ""
    return QueryRecord(
        ts=time.time(),
        question=question,
        kind=answer.kind,
        latency_s=round(latency_s, 3),
        pages=pages,
        manual_id=manual_id,
        revision="",
        model=model,
    )


def stats(records: Iterable[QueryRecord]) -> dict[str, object]:
    """Aggregate view: totals by kind, success rate, and latency quantiles."""
    items = list(records)
    total = len(items)
    by_kind: dict[str, int] = {}
    for record in items:
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
    answered = by_kind.get("answer", 0)
    latencies = sorted(record.latency_s for record in items)

    def _quantile(fraction: float) -> float:
        if not latencies:
            return 0.0
        index = min(int(fraction * len(latencies)), len(latencies) - 1)
        return round(latencies[index], 3)

    return {
        "total": total,
        "by_kind": by_kind,
        "success_rate": round(answered / total, 4) if total else 0.0,
        "avg_latency_s": round(sum(latencies) / total, 3) if total else 0.0,
        "p50_latency_s": _quantile(0.50),
        "p95_latency_s": _quantile(0.95),
    }
