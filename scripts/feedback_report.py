#!/usr/bin/env python3
"""Weekly operator-feedback triage report (operator-feedback spec).

Reads the feedback vote lines from the operator query log and lists the
topics worth a second look, worst first: thumbs-down count descending,
ties broken alphabetically, over a time window (default: last 7 days,
top 10 topics). Topic keys are deterministic local keyword reductions
(``keyword_variant`` over the normalized question) — no LLM, no new
services, same bytes in, same bytes out.

``--signals`` switches to the detection view (roadmap item 4, phase 1):
decline-strong-pool events, same window and ordering, with the pool size
and pages of the most recent occurrence per topic.

Usage:
    python scripts/feedback_report.py
    python scripts/feedback_report.py --log logs/queries.jsonl --days 14 --top 5
    python scripts/feedback_report.py --json
    python scripts/feedback_report.py --signals

Exit codes:
    0 - report produced (an empty window is still a report).
    1 - the log is missing or unreadable: fail loudly, never a silent
        empty report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# src layout, package not installed: make ``plant_rag`` importable when this
# file runs directly (``python scripts/feedback_report.py``) from a fresh clone.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from plant_rag import config
from plant_rag.answer.cache import normalize
from plant_rag.monitoring import FeedbackRecord, QueryLog
from plant_rag.retrieve.expand import keyword_variant

_DAY_S = 86400


def topic_key(question: str) -> str:
    """The question's deterministic topic: content keywords, else normalized text.

    ``keyword_variant`` strips Spanish question scaffolding (accents off,
    lowercase); when nothing contentful differs from the raw question the
    normalized question itself is the topic. Pure string math — the same
    question always maps to the same topic, on any machine.
    """
    return keyword_variant(question) or normalize(question)


def aggregate(
    records: list[FeedbackRecord], *, now: float, days: int, top: int
) -> list[dict]:
    """Vote counts per topic inside the window, most downs first.

    Window: ``ts >= now - days * 86400``. Ordering: down-count descending,
    then topic alphabetically (deterministic for equal down-counts). The
    result is capped at ``top`` rows; invalid vote values are skipped the
    same way every other reader skips what it cannot trust.
    """
    cutoff = now - days * _DAY_S
    counts: dict[str, dict[str, int]] = {}
    for record in records:
        if record.ts < cutoff or record.vote not in ("up", "down"):
            continue
        topic = topic_key(record.question)
        bucket = counts.setdefault(topic, {"up": 0, "down": 0})
        bucket[record.vote] += 1
    rows = [
        {"topic": topic, "up": bucket["up"], "down": bucket["down"]}
        for topic, bucket in counts.items()
    ]
    rows.sort(key=lambda row: (-row["down"], row["topic"]))
    return rows[: max(top, 0)]


def format_table(rows: list[dict], *, days: int, top: int) -> str:
    """Plain-text report; spacing derives only from the rows themselves."""
    title = f"Feedback report - last {days} days, top {top} topics"
    if not rows:
        return f"{title}\nNo feedback votes in the last {days} days."
    width = max(len("topic"), max(len(row["topic"]) for row in rows))
    lines = [title, "", f"{'topic':<{width}}  down   up"]
    lines.append("-" * (width + 11))
    for row in rows:
        lines.append(f"{row['topic']:<{width}}  {row['down']:>4} {row['up']:>3}")
    return "\n".join(lines)


def _load_feedback(path: str | Path) -> list[FeedbackRecord] | None:
    """Vote lines from the log, or ``None`` when the log cannot be read.

    Unlike the in-process readers (best-effort, "a failure never breaks an
    answer"), the report is invoked DELIBERATELY: an operator asking for a
    report on a missing or unreadable log must get an error, never an
    empty report that reads as "no complaints".
    """
    log = Path(path)
    try:
        with log.open("r", encoding="utf-8") as handle:
            handle.read(1)
    except OSError as exc:
        print(f"error: cannot read log {log}: {exc}", file=sys.stderr)
        return None
    return QueryLog(log).feedback_records()


def aggregate_signals(
    events: list[dict], *, now: float, days: int, top: int
) -> list[dict]:
    """Decline-strong-pool events per topic inside the window, worst first.

    Same window and ordering rules as ``aggregate`` (count descending, ties
    alphabetical), so both views sort identically. Each row carries the pool
    size and pages of the most recent occurrence for inspection.
    """
    cutoff = now - days * _DAY_S
    buckets: dict[str, dict] = {}
    for event in events:
        if event.get("event") != "decline-strong-pool":
            continue
        ts = event.get("ts")
        if not isinstance(ts, (int, float)) or ts < cutoff:
            continue
        topic = topic_key(str(event.get("question", "")))
        bucket = buckets.setdefault(
            topic,
            {"topic": topic, "count": 0, "pool_size": 0, "pages": [], "last_ts": 0.0},
        )
        bucket["count"] += 1
        if ts >= bucket["last_ts"]:
            bucket["last_ts"] = float(ts)
            bucket["pool_size"] = event.get("pool_size", 0)
            bucket["pages"] = list(event.get("pages", []))
    rows = sorted(buckets.values(), key=lambda row: (-row["count"], row["topic"]))
    return rows[: max(top, 0)]


def format_signals(rows: list[dict], *, days: int, top: int) -> str:
    """Plain-text decline-signal report; spacing derives only from the rows."""
    title = f"Decline signals - last {days} days, top {top} topics"
    if not rows:
        return f"{title}\nNo decline-with-strong-pool events in the last {days} days."
    width = max(len("topic"), max(len(row["topic"]) for row in rows))
    lines = [title, "", f"{'topic':<{width}}  count  pool  pages"]
    lines.append("-" * (width + 22))
    for row in rows:
        pages = ",".join(str(page) for page in row["pages"]) or "-"
        lines.append(
            f"{row['topic']:<{width}}  {row['count']:>5}  {row['pool_size']:>4}  {pages}"
        )
    return "\n".join(lines)


def _load_events(path: str | Path) -> list[dict] | None:
    """Event lines from the log, or ``None`` when the log cannot be read.

    Same fail-loud contract as ``_load_feedback``: a report on an unreadable
    log must error, never look like "no signals".
    """
    log = Path(path)
    try:
        with log.open("r", encoding="utf-8") as handle:
            handle.read(1)
    except OSError as exc:
        print(f"error: cannot read log {log}: {exc}", file=sys.stderr)
        return None
    return QueryLog(log).event_records()


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aggregate operator feedback votes into a triage report."
    )
    parser.add_argument(
        "--log",
        default=config.QUERY_LOG_PATH,
        help="path to the operator query log (default: QUERY_LOG_PATH)",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="window size in days (default: 7)"
    )
    parser.add_argument(
        "--top", type=int, default=10, help="maximum topics listed (default: 10)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit JSON instead of the plain-text table",
    )
    parser.add_argument(
        "--signals",
        action="store_true",
        help=(
            "report decline-strong-pool events (detection view) instead of "
            "feedback votes"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.signals:
        events = _load_events(args.log)
        if events is None:
            return 1
        now = time.time()
        rows = aggregate_signals(events, now=now, days=args.days, top=args.top)
        if args.as_json:
            print(
                json.dumps(
                    {
                        "days": args.days,
                        "top": args.top,
                        "signals": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(format_signals(rows, days=args.days, top=args.top))
        return 0
    records = _load_feedback(args.log)
    if records is None:
        return 1
    now = time.time()
    rows = aggregate(records, now=now, days=args.days, top=args.top)
    if args.as_json:
        print(
            json.dumps(
                {"days": args.days, "top": args.top, "topics": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_table(rows, days=args.days, top=args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
