"""Operator-query log: append-only JSONL records and aggregate stats."""

from __future__ import annotations

import json

import pytest

from plant_rag.models import Answer, Citation
from plant_rag.monitoring import QueryLog, QueryRecord, build_record, stats


def _answer(kind: str, pages: tuple[tuple[str, int], ...] = ()) -> Answer:
    citations = tuple(Citation(manual, page) for manual, page in pages)
    text = "answer" if kind == "answer" else "refusal"
    return Answer(kind=kind, text_es=text, citations=citations)


def test_record_round_trips_through_one_jsonl_line(tmp_path) -> None:
    log = QueryLog(tmp_path / "logs" / "queries.jsonl")
    answer = _answer("answer", (("Owners_Manual", 98),))

    log.record(build_record("presion neumaticos", answer, 1.234, model="qwen2.5:7b-instruct"))

    lines = (tmp_path / "logs" / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["question"] == "presion neumaticos"
    assert data["kind"] == "answer"
    assert data["pages"] == [98]
    assert data["manual_id"] == "Owners_Manual"
    assert data["model"] == "qwen2.5:7b-instruct"
    assert data["latency_s"] == 1.234
    # The answer TEXT is never logged, only facts about it.
    assert "answer" not in {k: v for k, v in data.items() if k != "kind"}.get("text", "")


def test_phase_timings_round_trip_through_the_log(tmp_path) -> None:
    # M4: additive phase timings, unrounded, next to the rounded total.
    log = QueryLog(tmp_path / "queries.jsonl")
    phases = {"embed_s": 0.0123, "qdrant_s": 0.0456, "rerank_s": 0.789, "llm_s": 4.5}

    log.record(
        build_record(
            "presion neumaticos",
            _answer("answer", (("M", 1),)),
            5.4,
            model="m",
            phases=phases,
        )
    )

    line = (tmp_path / "queries.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert json.loads(line)["phases"] == phases
    records = log.records()
    assert records[0].phases == phases
    assert sum(records[0].phases.values()) <= 5.4  # never more than the total


def test_a_record_without_timings_keeps_its_historical_shape(tmp_path) -> None:
    # The key must be ABSENT (not null): a reader that predates the field sees
    # exactly the line it has always seen.
    log = QueryLog(tmp_path / "queries.jsonl")

    log.record(build_record("q", _answer("refusal"), 0.2, model="m"))

    line = (tmp_path / "queries.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert "phases" not in json.loads(line)
    assert log.records()[0].phases is None


def test_a_line_written_before_phase_timings_still_parses(tmp_path) -> None:
    # The exact trap: the reader projects the line onto the required schema, so
    # making the new field required would silently drop the whole history.
    path = tmp_path / "queries.jsonl"
    legacy = {
        "ts": 1.0,
        "question": "pregunta antigua",
        "kind": "answer",
        "latency_s": 2.5,
        "pages": [7],
        "manual_id": "Owners_Manual",
        "revision": "r3",
        "model": "m",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    records = QueryLog(path).records()

    assert len(records) == 1
    assert records[0].question == "pregunta antigua"
    assert records[0].phases is None
    assert stats(records)["total"] == 1  # history still counts


def test_reading_back_a_corrupt_line_skips_it_instead_of_failing(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record(build_record("q1", _answer("answer", (("M", 1),)), 1.0, model="m"))
    path.write_text(
        path.read_text(encoding="utf-8") + "{not json}\n", encoding="utf-8"
    )

    records = log.records()

    assert len(records) == 1
    assert records[0].question == "q1"


def test_stats_aggregates_kinds_success_rate_and_latency(tmp_path) -> None:
    log = QueryLog(tmp_path / "queries.jsonl")
    log.record(build_record("a", _answer("answer", (("M", 1),)), 1.0, model="m"))
    log.record(build_record("b", _answer("answer", (("M", 2),)), 3.0, model="m"))
    log.record(build_record("c", _answer("refusal"), 0.5, model="m"))

    result = stats(log.records())

    assert result["total"] == 3
    assert result["by_kind"] == {"answer": 2, "refusal": 1}
    assert result["success_rate"] == 0.6667
    assert result["avg_latency_s"] == 1.5
    assert result["p50_latency_s"] == 1.0


def test_stats_of_an_empty_log_is_all_zeros(tmp_path) -> None:
    assert stats(QueryLog(tmp_path / "absent.jsonl").records()) == {
        "total": 0,
        "by_kind": {},
        "success_rate": 0.0,
        "avg_latency_s": 0.0,
        "p50_latency_s": 0.0,
        "p95_latency_s": 0.0,
    }


def test_record_swallows_write_failures(tmp_path, monkeypatch) -> None:
    # A logging failure must never break an answer: writing to a path whose
    # parent is a FILE raises, and the record call must survive it.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    log = QueryLog(blocker / "nested" / "queries.jsonl")

    log.record(build_record("q", _answer("refusal"), 0.1, model="m"))  # no raise


def test_refusal_records_carry_no_pages(tmp_path) -> None:
    record = build_record("presidente", _answer("refusal"), 0.2, model="m")

    assert record.pages == ()
    assert record.manual_id == ""
    assert isinstance(record, QueryRecord)


# ---------------------------------------------------------------------------
# tolerant reads: answer history survives the introduction of feedback events
# ---------------------------------------------------------------------------


def _raw_answer_line(question: str, *, ts: float, kind: str = "answer") -> str:
    """One pre-change answer line, in the exact shape the old writer emitted."""
    return json.dumps(
        {
            "ts": ts,
            "question": question,
            "kind": kind,
            "latency_s": 1.5,
            "pages": [98],
            "manual_id": "Owners_Manual",
            "revision": "",
            "model": "m",
        }
    )


def _raw_feedback_line(question: str, *, ts: float, vote: str = "up") -> str:
    """One vote line as the (future) feedback writer will emit it."""
    return json.dumps(
        {
            "event": "feedback",
            "ts": ts,
            "question": question,
            "kind": "answer",
            "vote": vote,
            "model": "m",
        }
    )


def test_pre_change_answer_lines_read_back_unchanged(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(
        _raw_answer_line("presion neumaticos", ts=1000.0)
        + "\n"
        + _raw_answer_line("¿qué es el bypass?", ts=2000.0, kind="refusal")
        + "\n",
        encoding="utf-8",
    )

    records = QueryLog(path).records()

    assert [r.question for r in records] == [
        "presion neumaticos",
        "¿qué es el bypass?",
    ]
    assert records[0].latency_s == 1.5
    assert list(records[0].pages) == [98]
    assert records[1].kind == "refusal"


def test_mixed_log_returns_answer_lines_and_excludes_feedback(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(
        "\n".join(
            [
                _raw_answer_line("primera pregunta", ts=1000.0),
                _raw_feedback_line("primera pregunta", ts=1500.0, vote="up"),
                _raw_answer_line("segunda pregunta", ts=2000.0),
                _raw_feedback_line("segunda pregunta", ts=2500.0, vote="down"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = QueryLog(path).records()

    assert [r.question for r in records] == ["primera pregunta", "segunda pregunta"]


def test_stats_count_answers_only_in_a_mixed_log(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    path.write_text(
        "\n".join(
            [
                _raw_answer_line("buena pregunta", ts=1000.0),
                _raw_feedback_line("buena pregunta", ts=1500.0, vote="up"),
                _raw_answer_line("mala pregunta", ts=2000.0, kind="refusal"),
                _raw_feedback_line("mala pregunta", ts=2500.0, vote="down"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = stats(QueryLog(path).records())

    assert result["total"] == 2
    assert result["by_kind"] == {"answer": 1, "refusal": 1}
    assert result["success_rate"] == 0.5


# ---------------------------------------------------------------------------
# feedback write path: votes are appended, read back, and never leak
# ---------------------------------------------------------------------------


def test_feedback_vote_round_trips_alongside_the_answer_it_rates(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record(
        build_record("presion neumaticos", _answer("answer", (("M", 98),)), 1.2, model="m")
    )

    log.record_feedback(
        "presion neumaticos", "answer", "up", model="qwen2.5:7b-instruct"
    )

    votes = log.feedback_records()
    assert len(votes) == 1
    assert votes[0].question == "presion neumaticos"
    assert votes[0].kind == "answer"
    assert votes[0].vote == "up"
    assert votes[0].model == "qwen2.5:7b-instruct"

    # Same file, two readers, zero bleed: the vote is a feedback event line.
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert line["event"] == "feedback"
    assert len(log.records()) == 1
    assert log.records()[0].question == "presion neumaticos"


def test_feedback_records_skip_answer_lines_and_corruption(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record(build_record("respuesta", _answer("answer", (("M", 1),)), 1.0, model="m"))
    log.record_feedback("mala respuesta", "refusal", "down", model="m")
    path.write_text(
        path.read_text(encoding="utf-8") + "{not json}\n", encoding="utf-8"
    )

    votes = log.feedback_records()

    assert [v.question for v in votes] == ["mala respuesta"]
    assert votes[0].vote == "down"
    assert votes[0].kind == "refusal"


def test_invalid_vote_is_rejected_and_never_written(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)

    with pytest.raises(ValueError, match="vote"):
        log.record_feedback("q", "answer", "sideways", model="m")

    assert log.feedback_records() == []
    assert not path.exists()


def test_feedback_write_failure_never_raises(tmp_path) -> None:
    # A failed vote must not block the operator: writing under a FILE
    # raises at mkdir/open, and record_feedback must swallow it.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    log = QueryLog(blocker / "nested" / "queries.jsonl")

    log.record_feedback("q", "answer", "down", model="m")  # no raise


def test_feedback_records_of_an_absent_log_is_empty(tmp_path) -> None:
    assert QueryLog(tmp_path / "absent.jsonl").feedback_records() == []


# ---------------------------------------------------------------------------
# decline-signal write path (roadmap item 4, phase 1: detection only)
# ---------------------------------------------------------------------------


def _decline_hits() -> tuple:
    """A full page-diverse pool, the shape a strong-pool decline sees."""
    from plant_rag.models import Hit

    return tuple(
        Hit(manual_id="Owners_Manual", page=page, revision="r3", child="c", parent="p")
        for page in (98, 99, 205, 206, 294)
    )


def test_decline_signal_round_trips_with_its_pool(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record(
        build_record(
            "como configuro el limite de velocidad",
            _answer("refusal", ()),
            2.0,
            model="m",
        )
    )

    log.record_decline_signal(
        "como configuro el limite de velocidad", _decline_hits(), model="m"
    )

    events = log.event_records("decline-strong-pool")
    assert len(events) == 1
    assert events[0]["question"] == "como configuro el limite de velocidad"
    assert events[0]["pool_size"] == 5
    assert events[0]["pages"] == [98, 99, 205, 206, 294]
    assert events[0]["manual_id"] == "Owners_Manual"
    assert events[0]["revision"] == "r3"

    # Three schemas, one file, zero bleed: stats see the answer only.
    assert len(log.records()) == 1
    assert log.feedback_records() == []


def test_event_records_can_list_every_event_type(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record_feedback("q1", "answer", "down", model="m")
    log.record_decline_signal("q2", _decline_hits(), model="m")

    kinds = sorted(event["event"] for event in log.event_records())

    assert kinds == ["decline-strong-pool", "feedback"]


def test_decline_signal_write_failure_never_raises(tmp_path) -> None:
    # Detection must never block or break an answer: same best-effort rule.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    log = QueryLog(blocker / "nested" / "queries.jsonl")

    log.record_decline_signal("q", _decline_hits(), model="m")  # no raise


# ---------------------------------------------------------------------------
# citation-quality events (Prop #1: measurement only, separate event line)
# ---------------------------------------------------------------------------


def test_citation_quality_round_trips_as_its_own_event(tmp_path) -> None:
    path = tmp_path / "queries.jsonl"
    log = QueryLog(path)
    log.record_citation_quality(
        "que es el tia",
        {"red_flag": True, "total_citations": 14, "unique_pages": 1},
        model="m",
    )

    events = log.event_records("citation-quality")
    assert len(events) == 1
    assert events[0]["question"] == "que es el tia"
    assert events[0]["red_flag"] is True
    assert events[0]["total_citations"] == 14
    assert events[0]["model"] == "m"

    # Separate schema, zero bleed: answers and other events are untouched.
    assert log.records() == []
    assert log.event_records("decline-strong-pool") == []


def test_citation_quality_write_failure_never_raises(tmp_path) -> None:
    # Measurement must never block or break an answer: same best-effort rule.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    log = QueryLog(blocker / "nested" / "queries.jsonl")

    log.record_citation_quality("q", {"red_flag": True}, model="m")  # no raise
