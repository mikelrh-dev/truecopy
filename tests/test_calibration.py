"""Per-manual calibration record model contracts (min-score-calibration spec).

Hermetic by contract: stdlib JSONL under ``tmp_path``, pure calls, no services.
Covers the v1 record round-trip, append-only guarantee, latest-wins reads,
corruption tolerance, the staleness matrix, the Spanish suggestion/advisory
wording contract, the dense-branch-only documentation scope, and the
``CALIBRATION_PATH`` config knob.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from plant_rag import calibration
from plant_rag import config
from plant_rag.calibration import (
    advisory,
    append_record,
    build_record,
    latest_record,
    read_records,
    staleness,
    suggest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDED_AT = "2026-10-01T12:00:00+00:00"
# Fixed evaluation instant for age assertions: the matrix must not rot as the
# real clock advances past the 90-day boundary.
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
CACHE_PATH = "logs/answer-cache.jsonl"

V1_KEYS = {
    "manual_id",
    "revision",
    "recommended_min_score",
    "current_min_score",
    "in_corpus_hits",
    "in_total",
    "out_of_corpus_hits",
    "out_total",
    "golden_pass",
    "golden_total",
    "golden_set_present",
    "sweep",
    "separates_labeled_set",
    "recorded_at",
    "source",
}


def _build(**overrides) -> dict:
    kwargs: dict = {
        "current_min_score": 0.40,
        "in_corpus_hits": 5,
        "in_total": 5,
        "out_of_corpus_hits": 0,
        "out_total": 5,
        "recommended_min_score": 0.50,
        "golden_pass": 7,
        "golden_total": 8,
        "golden_set_present": True,
        "sweep": [
            {"threshold": 0.40, "in_hits": 4, "out_hits": 0},
            {"threshold": 0.50, "in_hits": 5, "out_hits": 0},
        ],
        "source": "eval",
        "recorded_at": RECORDED_AT,
    }
    kwargs.update(overrides)
    manual_id = kwargs.pop("manual_id", "828D")
    revision = kwargs.pop("revision", "r2")
    return build_record(manual_id, revision, **kwargs)


def _append_one(path: Path, **overrides) -> dict:
    record = _build(**overrides)
    assert append_record(str(path), record) is True
    return record


# --- 1.1 record round-trip, append-only, latest-wins, corruption -----------


def test_record_round_trip_preserves_every_v1_field(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "calibration.jsonl"
    record = _build()

    assert append_record(str(path), record) is True

    assert read_records(str(path)) == [record]
    assert set(record) == V1_KEYS
    assert record["separates_labeled_set"] is True
    assert record["recorded_at"].endswith("+00:00")  # never Z (3.10 fromisoformat)


def test_append_never_rewrites_prior_lines(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path)
    before = path.read_bytes()

    _append_one(
        path, recommended_min_score=0.55, recorded_at="2026-10-02T12:00:00+00:00"
    )

    after = path.read_bytes()
    assert after.startswith(before)  # byte-identical prefix: pure append
    assert len(after.splitlines()) == 2


def test_append_writes_utf8_unescaped(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, manual_id="Manual_ñ_ü")

    raw = path.read_bytes()
    assert "ñ".encode("utf-8") in raw  # ensure_ascii=False
    assert b"\\u00f1" not in raw


def test_append_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "deep" / "calibration.jsonl"

    assert append_record(str(path), _build()) is True
    assert path.is_file()


def test_append_failure_is_best_effort_and_never_raises(tmp_path: Path) -> None:
    # The parent "directory" is a file: mkdir must fail, the seam must not.
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("not a directory", encoding="utf-8")

    assert append_record(str(blocker / "calibration.jsonl"), _build()) is False


def test_latest_record_wins_by_recorded_at_and_ties_break_to_the_last_line(
    tmp_path: Path,
) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, revision="r1", recommended_min_score=0.45)
    _append_one(
        path,
        revision="r2",
        recommended_min_score=0.50,
        recorded_at="2026-10-02T12:00:00+00:00",
    )
    _append_one(
        path,
        manual_id="Owners_Manual",
        recommended_min_score=0.60,
        recorded_at="2026-10-09T12:00:00+00:00",
    )

    latest = latest_record(str(path), "828D")
    assert latest is not None
    assert latest["revision"] == "r2"
    assert latest["recommended_min_score"] == 0.50

    # Tie on recorded_at: the LAST line wins.
    _append_one(
        path,
        revision="r3",
        recommended_min_score=0.55,
        recorded_at="2026-10-02T12:00:00+00:00",
    )
    tied = latest_record(str(path), "828D")
    assert tied is not None
    assert tied["revision"] == "r3"


def test_key_miss_never_fuzzy_matches_a_similar_id(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, manual_id="828D")

    assert latest_record(str(path), "828D_extra") is None
    assert latest_record(str(path), "owners_manual") is None


def test_corrupt_lines_are_skipped_and_valid_ones_survive(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    good = _build()
    path.write_text(
        "{not json}\n" + json.dumps(good) + "\n" + '["not", "a", "record"]\n',
        encoding="utf-8",
    )

    assert read_records(str(path)) == [good]


def test_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert read_records(str(tmp_path / "absent.jsonl")) == []
    assert latest_record(str(tmp_path / "absent.jsonl"), "828D") is None


def test_empty_path_disables_every_seam(tmp_path: Path) -> None:
    assert append_record("", _build()) is False
    assert read_records("") == []
    assert latest_record("", "828D") is None
    assert advisory("", "828D", "r2", cache_path=CACHE_PATH) is None
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere


# --- 1.3 staleness matrix --------------------------------------------------


def _aged_stamp(days: int) -> datetime:
    return NOW - timedelta(days=days)


def test_staleness_fresh_when_revision_and_labels_match_and_age_is_in_budget() -> None:
    record = _build(recorded_at=_aged_stamp(10).isoformat())
    assert (
        staleness(
            record,
            current_revision="r2",
            current_label_totals={"in_total": 5, "out_total": 5},
            now=NOW,
        )
        == "fresh"
    )


def test_staleness_revision_mismatch_is_hard_stale() -> None:
    assert staleness(_build(), current_revision="r3", now=NOW) == "hard-stale"


def test_staleness_label_drift_is_hard_stale() -> None:
    drifted = staleness(
        _build(),
        current_revision="r2",
        current_label_totals={"in_total": 6, "out_total": 5},
        now=NOW,
    )
    assert drifted == "hard-stale"


def test_staleness_hard_beats_soft_when_the_revision_already_moved() -> None:
    record = _build(recorded_at=_aged_stamp(120).isoformat())
    assert staleness(record, current_revision="r1", now=NOW) == "hard-stale"


def test_staleness_age_over_90_days_is_soft_stale() -> None:
    record = _build(recorded_at=_aged_stamp(91).isoformat())
    assert staleness(record, current_revision="r2", now=NOW) == "soft-stale"


def test_staleness_exactly_90_days_is_still_fresh() -> None:
    record = _build(recorded_at=_aged_stamp(90).isoformat())
    assert staleness(record, current_revision="r2", now=NOW) == "fresh"


def test_staleness_defaults_now_to_the_current_instant() -> None:
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    aged = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()

    assert staleness(_build(recorded_at=recent), current_revision="r2") == "fresh"
    assert staleness(_build(recorded_at=aged), current_revision="r2") == "soft-stale"


# --- 1.4 suggestion / advisory contracts -----------------------------------


def test_suggest_carries_every_contract_field() -> None:
    text = suggest(_build(), "fresh", cache_path=CACHE_PATH)

    assert f"suelo recomendado={0.50}" in text
    assert f"actual={0.40}" in text
    assert "(revisión r2)" in text
    assert RECORDED_AT in text
    assert "golden 7/8" in text
    assert "en corpus 5/5" in text
    assert "fuera 0/5" in text
    assert "[fresh]" in text
    assert "separates this labeled set" in text  # verbatim identifier token
    assert CACHE_PATH in text  # floors differ -> wipe instruction
    assert "borre" in text
    assert "optimal" not in text.lower()


def test_suggest_omits_the_wipe_instruction_when_the_floors_match() -> None:
    text = suggest(_build(recommended_min_score=0.40), "fresh", cache_path=CACHE_PATH)

    assert "borre" not in text
    assert CACHE_PATH not in text
    assert "separates this labeled set" in text


def test_suggest_without_a_separating_threshold_still_carries_the_token() -> None:
    text = suggest(
        _build(recommended_min_score=None), "soft-stale", cache_path=CACHE_PATH
    )

    assert "ningún" in text
    assert "separates this labeled set" in text
    assert f"actual={0.40}" in text
    assert "borre" not in text
    assert "[soft-stale]" in text


def test_suggest_reports_missing_golden_counts_as_undetermined() -> None:
    text = suggest(
        _build(golden_pass=None, golden_total=None, golden_set_present=False),
        "fresh",
        cache_path=CACHE_PATH,
    )

    assert "golden n/d" in text


def test_advisory_is_none_only_when_the_store_is_disabled() -> None:
    assert advisory("", "828D", "r2", cache_path=CACHE_PATH) is None


def test_advisory_without_any_record_reports_no_record_for_that_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, manual_id="Owners_Manual")

    text = advisory(str(path), "828D", "r2", cache_path=CACHE_PATH)

    assert text is not None
    assert "No hay registro" in text
    assert "'828D'" in text  # reported for that id only


def test_advisory_fresh_record_prints_the_suggestion(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _append_one(path, revision="r2", recorded_at=recent)

    text = advisory(str(path), "828D", "r2", cache_path=CACHE_PATH)

    assert text is not None
    assert "[fresh]" in text
    assert "separates this labeled set" in text
    assert "golden 7/8" in text


def test_advisory_revision_mismatch_downgrades_but_never_hides(
    tmp_path: Path,
) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, revision="r1")

    text = advisory(str(path), "828D", "r3", cache_path=CACHE_PATH)

    assert text is not None
    assert "[hard-stale]" in text
    # Downgraded wording still shows the value with counts and date.
    assert "separates this labeled set" in text
    assert RECORDED_AT in text
    assert "golden 7/8" in text


def test_advisory_aged_record_is_soft_stale(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    aged = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    _append_one(path, recorded_at=aged)

    text = advisory(str(path), "828D", "r2", cache_path=CACHE_PATH)

    assert text is not None
    assert "[soft-stale]" in text


def test_advisory_never_writes_to_the_store(tmp_path: Path) -> None:
    path = tmp_path / "calibration.jsonl"
    _append_one(path, revision="r1")
    before = path.read_bytes()

    advisory(str(path), "828D", "r3", cache_path=CACHE_PATH)

    assert path.read_bytes() == before  # read-only


# --- 1.5 dense-only documentation scope ------------------------------------


def test_module_docstring_states_dense_branch_only_and_text_ungated() -> None:
    doc = calibration.__doc__ or ""

    assert "dense branch" in doc
    assert "text branch" in doc
    assert "ungated" in doc


def test_nothing_in_the_module_or_a_suggestion_is_called_optimal() -> None:
    source = Path(calibration.__file__).read_text(encoding="utf-8")

    assert "optimal" not in source.lower()
    assert "optimal" not in suggest(_build(), "fresh", cache_path=CACHE_PATH).lower()


# --- Phase 2: config knob (CALIBRATION_PATH) -------------------------------


def test_calibration_path_defaults_to_the_logs_store() -> None:
    previous = os.environ.pop("PLANT_RAG_CALIBRATION_PATH", None)
    try:
        importlib.reload(config)
        assert config.CALIBRATION_PATH == "logs/calibration.jsonl"
    finally:
        if previous is not None:
            os.environ["PLANT_RAG_CALIBRATION_PATH"] = previous
        importlib.reload(config)


def test_calibration_path_env_override_and_empty_value_disables() -> None:
    previous = os.environ.get("PLANT_RAG_CALIBRATION_PATH")
    try:
        os.environ["PLANT_RAG_CALIBRATION_PATH"] = "custom/cal.jsonl"
        importlib.reload(config)
        assert config.CALIBRATION_PATH == "custom/cal.jsonl"

        os.environ["PLANT_RAG_CALIBRATION_PATH"] = ""
        importlib.reload(config)
        assert config.CALIBRATION_PATH == ""
    finally:
        if previous is None:
            os.environ.pop("PLANT_RAG_CALIBRATION_PATH", None)
        else:
            os.environ["PLANT_RAG_CALIBRATION_PATH"] = previous
        importlib.reload(config)


# --- Phase 5: anti-import guard (serving stays record-free) ----------------


def test_serving_packages_never_import_the_calibration_store() -> None:
    """``retrieve/`` and ``answer/`` must never import the calibration module.

    Eval informs; the owner applies. Serving resolution stays env over
    config default — records never participate in MIN_SCORE, and the
    advisory is CLI-only (ingest). Source-scan precedent:
    ``tests/test_pipeline.py`` (eval source assertions).
    """
    package_root = Path(calibration.__file__).resolve().parents[1]
    offenders: list[str] = []
    for package in ("retrieve", "answer"):
        for path in sorted((package_root / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if (
                "plant_rag.calibration" in source
                or "from plant_rag import calibration" in source
            ):
                offenders.append(str(path.relative_to(package_root)))
    assert offenders == []
