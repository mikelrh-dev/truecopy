"""Operator-query log: append-only JSONL records and aggregate stats."""

from __future__ import annotations

import json

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
