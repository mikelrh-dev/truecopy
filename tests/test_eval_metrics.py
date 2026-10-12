"""Ranking-metrics tests (Phase 1, slices 1-2). Hermetic: rank math only."""

from __future__ import annotations

from pathlib import Path

import pytest

from plant_rag.eval_metrics import (
    RankedQuery,
    build_report,
    first_rank,
    load_labeled_set,
    precision_at_k,
    recall_at_k,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LABELED_SET = REPO_ROOT / "eval" / "labeled-qa.json"


def row(
    query: str = "q",
    ranked: tuple[int, ...] = (),
    relevant: frozenset[int] = frozenset({7}),
    *,
    expect_refusal: bool = False,
) -> RankedQuery:
    return RankedQuery(
        query=query,
        ranked=ranked,
        relevant=relevant,
        expect_refusal=expect_refusal,
    )


# --- the rank primitive -----------------------------------------------------


def test_first_rank_is_one_based_and_reads_the_best_position() -> None:
    assert first_rank((3, 7, 9), frozenset({7})) == 2
    assert first_rank((7, 3, 9), frozenset({7})) == 1
    assert first_rank((3, 9), frozenset({7})) is None  # a miss is None, never 0


def test_first_rank_matches_any_relevant_page_in_a_region() -> None:
    # "The golden is the REGION": any page of the labeled region counts, and
    # the earliest one decides the rank.
    assert first_rank((40, 41, 12), frozenset({39, 40, 41})) == 1


# --- the metrics ------------------------------------------------------------


def test_miss_is_counted_in_the_denominator_not_as_rank_zero() -> None:
    rows = (
        row("hit", (7,), frozenset({7})),
        row("miss", (1, 2), frozenset({7})),
    )
    report = build_report(rows, k=2)

    assert report.hit_rate == 0.5
    assert report.mrr == 0.5  # 1.0 + 0.0, over TWO queries (not 1.0/1)
    assert report.ranks == {"hit": 1, "miss": None}


def test_metrics_are_measured_over_the_citable_window_k() -> None:
    # The relevant page sits at rank 3: inside K=3, outside K=2. A gain beyond
    # the window is not a gain, because the answer may not cite it.
    rows = (row("q", (1, 2, 7), frozenset({7})),)

    assert build_report(rows, k=3).hit_rate == 1.0
    assert build_report(rows, k=2).hit_rate == 0.0


def test_precision_divides_by_k_not_by_what_was_returned() -> None:
    # One relevant page returned out of K=5 slots is P@5 = 0.2, not 1.0.
    assert precision_at_k(row("q", (7,), frozenset({7})), 5) == 0.2
    assert precision_at_k(row("q", (7,), frozenset({7})), 1) == 1.0
    assert precision_at_k(row("q", (), frozenset({7})), 5) == 0.0


def test_recall_divides_by_the_labeled_pages_found() -> None:
    assert recall_at_k(row("q", (7, 8), frozenset({7, 8, 9})), 5) == pytest.approx(
        0.6667, abs=1e-4
    )


def test_recall_refuses_a_case_with_no_labeled_pages() -> None:
    # A refusal control carries no relevant pages by design, so R@K is
    # undefined for it: it is scored as a refusal hit, never averaged in.
    control = row("out-of-corpus", (), frozenset(), expect_refusal=True)
    with pytest.raises(ValueError, match="R@K undefined"):
        recall_at_k(control, 5)


def test_refusal_controls_are_scored_separately_not_averaged_in() -> None:
    rows = (
        row("hit", (7,), frozenset({7})),
        row("out-of-corpus", (), frozenset(), expect_refusal=True),
    )
    report = build_report(rows, k=5)

    assert report.hit_rate == 1.0  # the control neither helps nor hurts the mean
    assert report.refusal_ok == 1
    assert report.refusal_total == 1
    assert "hit" in report.ranks and "out-of-corpus" not in report.ranks


def test_refusal_control_with_a_non_empty_pool_is_not_a_refusal_hit() -> None:
    report = build_report(
        (row("out-of-corpus", (7,), frozenset(), expect_refusal=True),), k=5
    )

    assert report.refusal_ok == 0
    assert "pool NO vacío" in report.format_es()


def test_a_non_refusal_case_without_labels_fails_loudly() -> None:
    # A malformed label must not silently shrink the measured set.
    with pytest.raises(ValueError, match="labels no relevant pages"):
        RankedQuery("q", (1,), frozenset())


def test_report_round_trips_into_the_recordable_dict() -> None:
    report = build_report((row("q", (7,), frozenset({7})),), k=5)
    payload = report.to_dict()

    assert payload["k"] == 5
    assert payload["scored"] == 1
    assert payload["ranks"] == {"q": 1}
    assert set(("hit_rate", "mrr", "precision", "recall")) <= set(payload)


# --- the shipped labeled set -------------------------------------------------


def test_shipped_labeled_set_loads_with_its_written_criteria() -> None:
    rows, metadata = load_labeled_set(LABELED_SET)

    # The criteria is the actual slice-2 deliverable: numbers without the rule
    # that produced them are unreproducible.
    assert metadata["criteria"].strip()
    assert metadata["status"] == "seeded"
    assert len(rows) == 9
    assert {r.manual_id for r in rows} == {
        "Owners_Manual",
        "828D_DA_1015_en_en_US",
    }
    controls = [r for r in rows if r.expect_refusal]
    assert len(controls) == 1 and controls[0].relevant == frozenset()
    assert all(r.relevant for r in rows if not r.expect_refusal)


def test_labeled_set_loader_rejects_a_case_without_a_query(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"cases": [{"relevant_pages": [1]}]}', encoding="utf-8")

    with pytest.raises(ValueError, match="missing"):
        load_labeled_set(bad)


def test_labeled_set_loader_rejects_an_empty_case_list(tmp_path) -> None:
    bad = tmp_path / "empty.json"
    bad.write_text('{"cases": []}', encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        load_labeled_set(bad)
