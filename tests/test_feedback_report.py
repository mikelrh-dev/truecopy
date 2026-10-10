"""Weekly feedback report: deterministic windows, caps, ordering, fail-loud.

The report is a triage aid (operator-feedback spec): pure local string and
count math over the log's vote lines, no LLM, no services. These tests pin
the four contract surfaces: default window (7 days) / cap (top 10), flag
overrides, loud failure on an unreadable log, and deterministic ordering.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from plant_rag.monitoring import FeedbackRecord
from scripts.feedback_report import aggregate, format_table, main, topic_key

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "feedback_report.py"
DAY_S = 86400


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the entrypoint exactly as an operator would, from the repo root."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _write_log(path: Path, entries: list[tuple[str, str, float]]) -> Path:
    """Write feedback lines shaped as the writer emits them.

    ``entries`` are ``(question, vote, age_seconds)``; ages keep window
    assertions independent of the wall clock the CLI reads.
    """
    now = time.time()
    lines = [
        json.dumps(
            {
                "event": "feedback",
                "ts": now - age,
                "question": question,
                "kind": "answer",
                "vote": vote,
                "model": "m",
            },
            ensure_ascii=False,
        )
        for question, vote, age in entries
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_default_run_covers_seven_days_and_caps_at_ten_topics(tmp_path) -> None:
    names = ("uno", "dos", "tres", "cuatro", "cinco", "seis",
             "siete", "ocho", "nueve", "diez", "once", "doce")
    topics = [f"tema {name}" for name in names]
    entries = [(topic, "down", DAY_S) for topic in topics]
    # Outside the 7-day window AND heavily downvoted: if the window filter
    # broke, this topic would sort FIRST and the cap assertion would fail.
    entries += [("tema fuera de serie", "down", DAY_S * 9)] * 5
    log = _write_log(tmp_path / "queries.jsonl", entries)

    result = _run(["--log", str(log), "--json"])

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    data = json.loads(result.stdout)
    assert data["days"] == 7
    assert data["top"] == 10
    listed = [row["topic"] for row in data["topics"]]
    assert len(listed) == 10
    # Equal down-counts (1 each): alphabetical tiebreak, first 10 of 12.
    assert listed == sorted(topics)[:10]
    assert "tema fuera de serie" not in listed
    assert all(row["down"] == 1 for row in data["topics"])


def test_days_flag_overrides_the_default_window(tmp_path) -> None:
    log = _write_log(
        tmp_path / "queries.jsonl",
        [
            ("tema reciente", "down", DAY_S),
            ("tema antiguo", "down", DAY_S * 20),
            ("tema muy antiguo", "down", DAY_S * 40),
        ],
    )

    default = _run(["--log", str(log), "--json"])
    wide = _run(["--log", str(log), "--days", "30", "--json"])

    assert default.returncode == 0 and wide.returncode == 0
    assert [r["topic"] for r in json.loads(default.stdout)["topics"]] == [
        "tema reciente"
    ]
    wide_data = json.loads(wide.stdout)
    assert wide_data["days"] == 30
    listed = [r["topic"] for r in wide_data["topics"]]
    # Both have 1 down: alphabetical tiebreak.
    assert listed == ["tema antiguo", "tema reciente"]
    assert "tema muy antiguo" not in listed


def test_top_flag_caps_the_listing(tmp_path) -> None:
    log = _write_log(
        tmp_path / "queries.jsonl",
        ([("tema beta", "down", DAY_S)] * 3)
        + ([("tema gamma", "down", DAY_S)] * 2)
        + [("tema alpha", "down", DAY_S)],
    )

    capped = _run(["--log", str(log), "--days", "30", "--top", "2", "--json"])
    full = _run(["--log", str(log), "--days", "30", "--top", "3", "--json"])

    assert capped.returncode == 0 and full.returncode == 0
    assert [r["topic"] for r in json.loads(capped.stdout)["topics"]] == [
        "tema beta",
        "tema gamma",
    ]
    assert [r["topic"] for r in json.loads(full.stdout)["topics"]] == [
        "tema beta",
        "tema gamma",
        "tema alpha",
    ]


def test_unreadable_log_fails_loudly_instead_of_reporting_empty(tmp_path) -> None:
    missing = tmp_path / "no-such-log.jsonl"

    result = _run(["--log", str(missing)])

    assert result.returncode == 1
    assert "cannot read log" in result.stderr
    assert "Feedback report" not in result.stdout  # no silent empty report

    as_directory = _run(["--log", str(tmp_path)])
    assert as_directory.returncode == 1
    assert "cannot read log" in as_directory.stderr

    # Same contract at function level.
    assert main(["--log", str(missing)]) == 1


def test_aggregate_orders_by_downs_then_topic_and_respects_the_window() -> None:
    now = 1_800_000_000.0

    def rec(question: str, vote: str, age: float) -> FeedbackRecord:
        return FeedbackRecord(
            ts=now - age, question=question, kind="answer", vote=vote, model="m"
        )

    records = [
        rec("zeta fallo", "down", 100),
        rec("zeta fallo", "down", 200),
        rec("alpha fallo", "down", 100),
        rec("alpha fallo", "down", 200),
        rec("alpha fallo", "up", 300),
        rec("mid fallo", "down", 100),
        rec("mid fallo", "down", 200),
        rec("mid fallo", "down", 400),
        rec("mid fallo", "up", 300),
        rec("viejo fallo", "down", 10 * DAY_S),  # outside the window
    ]

    rows = aggregate(records, now=now, days=7, top=10)

    # Down-count desc (3, 2, 2), then alphabetical for the tie.
    assert [row["topic"] for row in rows] == [
        "mid fallo",
        "alpha fallo",
        "zeta fallo",
    ]
    assert rows[0] == {"topic": "mid fallo", "up": 1, "down": 3}
    assert rows[1]["up"] == 1 and rows[2]["up"] == 0
    assert "viejo fallo" not in [row["topic"] for row in rows]
    assert [row["topic"] for row in aggregate(records, now=now, days=7, top=2)] == [
        "mid fallo",
        "alpha fallo",
    ]


def test_topic_key_is_deterministic_across_surface_forms() -> None:
    # Same question, different accents/case/punctuation -> same topic.
    assert topic_key("¿Qué es el TIA?") == topic_key("que es el tia") == "tia"
    assert topic_key("¿presion neumaticos!") == topic_key("presion neumaticos")
    # Already-keywords questions fall back to the normalized question.
    assert topic_key("presion neumaticos") == "presion neumaticos"


def test_format_table_lists_rows_and_an_empty_window_says_so() -> None:
    text = format_table([{"topic": "tema beta", "up": 1, "down": 3}], days=7, top=10)

    assert "tema beta" in text
    assert "3" in text and "1" in text

    empty = format_table([], days=7, top=10)
    assert "No feedback votes" in empty


def test_help_exits_zero() -> None:
    result = _run(["--help"])

    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "usage:" in result.stdout.lower()
