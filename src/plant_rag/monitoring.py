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
* The same file doubles as the event store: votes are appended as
  ``{"event": "feedback"}`` lines and decline signals as
  ``{"event": "decline-strong-pool"}`` lines under the SAME lock. A second
  file would split one history in two, and every reader already tolerates
  the event schema (``records()`` branches on ANY event before any field
  access, so a future event type cannot be misread as a corrupt answer).
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

_FEEDBACK_FIELDS = (
    "ts",
    "question",
    "kind",
    "vote",
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


@dataclass(frozen=True)
class FeedbackRecord:
    """One operator vote — a feedback event, never an answer record.

    Same log, same lock, different schema: ``kind`` records what was rated
    (``answer``/``refusal``), ``vote`` is the rating itself, and no voter
    identity exists anywhere (log convention: facts about answers, never
    who typed them).
    """

    ts: float
    question: str
    kind: str
    vote: str
    model: str

    def to_json(self) -> str:
        payload = asdict(self)
        payload["event"] = "feedback"
        return json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class DeclineSignalRecord:
    """One "model declined while the pool looked full" event.

    Detection only (roadmap item 4, phase 1): it never changes an answer.
    ``pool_size`` is the number of page-diverse candidates handed to the LLM
    and ``pages`` their pages, so an owner can inspect what the model saw
    before declining. Same log, same lock, third schema.
    """

    ts: float
    question: str
    pool_size: int
    pages: tuple[int, ...]
    manual_id: str
    revision: str
    model: str

    def to_json(self) -> str:
        payload = asdict(self)
        payload["event"] = "decline-strong-pool"
        return json.dumps(payload, ensure_ascii=False)


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
                        if isinstance(data, dict) and data.get("event") is not None:
                            # Event lines (votes, decline signals) are a
                            # DIFFERENT schema. Branch on the event BEFORE
                            # any _FIELDS access so they are excluded by
                            # construction, never misread as corrupt answers
                            # (operator-feedback spec: "Feedback records
                            # MUST NOT count toward answer stats").
                            continue
                        parsed.append(QueryRecord(**{k: data[k] for k in _FIELDS}))
                    except Exception:  # noqa: BLE001 - skip corrupt lines
                        continue
        except Exception:  # noqa: BLE001 - an unreadable log reads as empty
            return []
        return parsed

    def record_feedback(
        self, question: str, kind: str, vote: str, *, model: str
    ) -> None:
        """Append one vote event under the same lock as the answer records.

        Validation raises (a programming error must never be swallowed);
        the write itself is best-effort, exactly like ``record``: a failed
        vote must not block or break the operator.
        """
        if vote not in ("up", "down"):
            raise ValueError(f"vote must be 'up' or 'down', got {vote!r}")
        record = FeedbackRecord(
            ts=time.time(),
            question=question,
            kind=kind,
            vote=vote,
            model=model,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(record.to_json() + "\n")
        except Exception:  # noqa: BLE001 - a failed vote write never blocks
            pass

    def record_decline_signal(
        self, question: str, hits: tuple, *, model: str
    ) -> None:
        """Append one decline-strong-pool event; best-effort like every write.

        ``hits`` is the page-diverse pool the LLM saw before declining. The
        event is DETECTION ONLY: it never changes an answer and never feeds
        back into serving (roadmap item 4, phase 1).
        """
        record = DeclineSignalRecord(
            ts=time.time(),
            question=question,
            pool_size=len(hits),
            pages=tuple(dict.fromkeys(hit.page for hit in hits)),
            manual_id=hits[0].manual_id if hits else "",
            revision=hits[0].revision if hits else "",
            model=model,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(record.to_json() + "\n")
        except Exception:  # noqa: BLE001 - a failed signal never blocks
            pass

    def event_records(self, event: str | None = None) -> list[dict]:
        """Every parseable event line (votes, decline signals), raw dicts.

        ``event`` filters by type when given (``"feedback"``,
        ``"decline-strong-pool"``). Answer lines and corruption are skipped.
        """
        if not self.path.is_file():
            return []
        parsed: list[dict] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not (
                            isinstance(data, dict)
                            and data.get("event") is not None
                        ):
                            continue
                        if event is not None and data.get("event") != event:
                            continue
                        parsed.append(data)
                    except Exception:  # noqa: BLE001 - skip corrupt lines
                        continue
        except Exception:  # noqa: BLE001 - an unreadable log reads as empty
            return []
        return parsed
    def feedback_records(self) -> list[FeedbackRecord]:
        """Every parseable vote line; answer lines and corruption are skipped."""
        if not self.path.is_file():
            return []
        parsed: list[FeedbackRecord] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not (
                            isinstance(data, dict)
                            and data.get("event") == "feedback"
                        ):
                            continue  # answers belong to records(), not here
                        parsed.append(
                            FeedbackRecord(**{k: data[k] for k in _FEEDBACK_FIELDS})
                        )
                    except Exception:  # noqa: BLE001 - skip corrupt lines
                        continue
        except Exception:  # noqa: BLE001 - an unreadable log reads as empty
            return []
        return parsed


def build_record(
    question: str,
    answer: Answer,
    latency_s: float,
    *,
    model: str,
    revision: str = "",
) -> QueryRecord:
    """The record for one answer: provenance from its citations, no content.

    ``revision`` is the corpus revision that produced the answer (``"r3"``).
    It defaults to the empty string so a caller that cannot know it keeps the
    historical shape; the answering chain always knows it and passes it, which
    is what lets the log answer "which corpus did this come from" after a
    reindex - the same provenance the calibration advisory warns about.
    """
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
        revision=revision,
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
