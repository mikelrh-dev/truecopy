"""Regression-gate tests (Phase 1, idea 4).

Hermetic: the comparison is pure, and the end-to-end run drives the gate
through a fake Qdrant plus an injected retrieval double. The plan's acceptance
is pinned here: the gate is green on the recorded measurement, and a synthetic
retrieval regression (the pool going empty, i.e. a lowered floor) turns it red.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plant_rag.eval_metrics import RankedQuery
from plant_rag.models import Hit
from scripts import eval_metrics as metrics_cli
from scripts import regression_gate


# --- doubles ---------------------------------------------------------------


class FakeClient:
    """Exactly the calls the gate makes to resolve the loaded manual."""

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(
                    alias_name="current_text", collection_name="manual_r12_text"
                )
            ]
        )

    def scroll(self, **kwargs):
        return [SimpleNamespace(payload={"manual_id": "Owners_Manual"})], None


class Retrieval:
    """Retrieval double: pages per query, empty when a query is not listed."""

    def __init__(self, pages: dict[str, int | None]) -> None:
        self.pages = pages

    def __call__(self, client, query):
        page = self.pages.get(query)
        if page is None:
            return ()
        return (Hit("Owners_Manual", page, "r12", "child", "parent"),)


ROWS = (
    RankedQuery("q1", (), frozenset({5}), manual_id="Owners_Manual"),
    RankedQuery("q2", (), frozenset({7}), manual_id="Owners_Manual"),
    RankedQuery(
        "out-of-corpus",
        (),
        frozenset(),
        expect_refusal=True,
        manual_id="Owners_Manual",
    ),
)

HEALTHY = {"q1": 5, "q2": 7, "out-of-corpus": None}
DEGRADED = {"q1": None, "q2": None, "out-of-corpus": None}  # pool emptied


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """The gate with its live seams replaced; returns (baseline_path, setter)."""
    baseline = tmp_path / "baseline.json"
    loader = lambda path: (ROWS, {"criteria": "regla"})  # noqa: E731
    monkeypatch.setattr(regression_gate, "load_labeled_set", loader)
    monkeypatch.setattr(metrics_cli, "load_labeled_set", loader)
    monkeypatch.setattr(regression_gate, "QdrantClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(metrics_cli, "QdrantClient", lambda **kwargs: FakeClient())
    state = {"retrieval": Retrieval(HEALTHY)}
    monkeypatch.setattr(metrics_cli, "retrieve_and_rank", state["retrieval"])

    def use(pages: dict[str, int | None]) -> None:
        monkeypatch.setattr(metrics_cli, "retrieve_and_rank", Retrieval(pages))

    return baseline, use


# --- the pure comparison ---------------------------------------------------


def test_compare_metrics_is_silent_when_nothing_moved() -> None:
    report = {"hit_rate": 0.8, "mrr": 0.6, "precision": 0.3, "recall": 0.7}

    assert regression_gate.compare_metrics(report, dict(report)) == []


def test_compare_metrics_reports_every_drop() -> None:
    baseline = {"hit_rate": 0.8, "mrr": 0.6, "precision": 0.3, "recall": 0.7}
    current = {"hit_rate": 0.6, "mrr": 0.5, "precision": 0.2, "recall": 0.7}

    regressions = regression_gate.compare_metrics(baseline, current)

    assert len(regressions) == 3
    assert any("hit_rate: 0.8 -> 0.6" in line for line in regressions)
    assert not any("recall" in line for line in regressions)


def test_compare_metrics_treats_a_worse_rank_and_a_lost_rank_as_regressions() -> None:
    baseline = {"ranks": {"q1": 1, "q2": 2}}
    current = {"ranks": {"q1": 3, "q2": None}}

    regressions = regression_gate.compare_metrics(baseline, current)

    assert any("rango 1 -> 3" in line for line in regressions)
    assert any("q2" in line and "fuera del top-K" in line for line in regressions)


def test_compare_metrics_reports_a_refusal_control_that_stopped_refusing() -> None:
    regressions = regression_gate.compare_metrics({"refusal_ok": 1}, {"refusal_ok": 0})

    assert any("fuera de corpus" in line for line in regressions)


def test_compare_checks_reports_a_flip_to_failing() -> None:
    regressions = regression_gate.compare_checks(
        {"checks": {"refusal (out-of-corpus)": True, "golden grounding": True}},
        {"checks": {"refusal (out-of-corpus)": True, "golden grounding": False}},
    )

    assert len(regressions) == 1
    assert "golden grounding" in regressions[0]


# --- the gate, end to end --------------------------------------------------


def test_gate_without_a_baseline_fails_loudly_never_passes(gate, capsys) -> None:
    baseline, _ = gate

    assert regression_gate.main(["--metrics-only", "--baseline", str(baseline)]) == 1
    assert "no hay baseline" in capsys.readouterr().err


def test_gate_records_a_baseline_and_is_green_on_the_same_measurement(
    gate, capsys
) -> None:
    baseline, _ = gate

    assert (
        regression_gate.main(
            ["--metrics-only", "--baseline", str(baseline), "--record"]
        )
        == 0
    )
    assert baseline.is_file()

    # ACCEPTANCE: green on the recorded measurement.
    assert regression_gate.main(["--metrics-only", "--baseline", str(baseline)]) == 0
    assert "GATE OK" in capsys.readouterr().out


def test_synthetic_retrieval_regression_turns_the_gate_red(gate, capsys) -> None:
    # ACCEPTANCE: lowering the floor empties the pool -> hit rate and MRR drop
    # below the baseline -> the gate must fail, not warn.
    baseline, use = gate
    assert (
        regression_gate.main(
            ["--metrics-only", "--baseline", str(baseline), "--record"]
        )
        == 0
    )

    use(DEGRADED)
    code = regression_gate.main(["--metrics-only", "--baseline", str(baseline)])
    err = capsys.readouterr().err

    assert code == 1
    assert "REGRESIONES" in err
    assert "hit_rate" in err


def test_tolerance_defaults_to_zero_so_a_real_drop_is_never_forgiven() -> None:
    baseline = {"hit_rate": 1.0}

    assert regression_gate.compare_metrics(baseline, {"hit_rate": 0.9}) != []
    assert (
        regression_gate.compare_metrics(baseline, {"hit_rate": 0.9}, tolerance=0.2)
        == []
    )


def test_gate_q2_lost_is_still_red_without_an_explicit_tolerance(gate) -> None:
    baseline, use = gate
    assert (
        regression_gate.main(
            ["--metrics-only", "--baseline", str(baseline), "--record"]
        )
        == 0
    )

    use({"q1": 5, "q2": None, "out-of-corpus": None})  # q2 lost: MRR drops

    assert regression_gate.main(["--metrics-only", "--baseline", str(baseline)]) == 1


def test_gate_fails_when_a_mandatory_check_is_red_in_head(
    gate, monkeypatch, capsys
) -> None:
    # A mandatory check that fails must never be baselined away; the gate must
    # also refuse to freeze a red HEAD as the reference.
    baseline, _ = gate
    monkeypatch.setattr(
        regression_gate,
        "run_live_checks",
        lambda client, golden_set: {
            "alarm retrieval": True,
            "cross-lingual ES->EN": False,
            "refusal": True,
            "golden grounding": True,
        },
    )

    assert regression_gate.main(["--baseline", str(baseline), "--record"]) == 1
    assert not baseline.is_file()  # a red HEAD is never recorded

    assert regression_gate.main(["--baseline", str(baseline)]) == 1
    assert "checks obligatorios en rojo" in capsys.readouterr().err


def test_gate_stays_green_when_every_check_passes(gate, monkeypatch, capsys) -> None:
    baseline, _ = gate
    monkeypatch.setattr(
        regression_gate,
        "run_live_checks",
        lambda client, golden_set: {
            "alarm retrieval": True,
            "cross-lingual ES->EN": True,
            "refusal": True,
            "golden grounding": True,
        },
    )

    assert regression_gate.main(["--baseline", str(baseline), "--record"]) == 0
    assert regression_gate.main(["--baseline", str(baseline)]) == 0
    assert "GATE OK" in capsys.readouterr().out


def test_gate_refuses_a_baseline_with_the_wrong_schema(gate, capsys) -> None:
    # The false-green guard: a baseline written by the OTHER CLI holds the bare
    # metrics shape, so it has no 'metrics' block. Comparing it as empty would
    # pass every regression.
    baseline, _ = gate
    baseline.write_text('{"k": 5, "hit_rate": 1.0}\n', encoding="utf-8")

    assert regression_gate.main(["--metrics-only", "--baseline", str(baseline)]) == 1
    assert "no trae bloque 'metrics'" in capsys.readouterr().err


def test_the_two_clis_do_not_share_a_baseline_path() -> None:
    # One shared path lets either --record overwrite the other's schema.
    assert metrics_cli.DEFAULT_BASELINE != regression_gate.DEFAULT_BASELINE


def test_metrics_cli_check_mode_reads_the_baseline(gate, capsys) -> None:
    # scripts/eval_metrics.py --record/--check is the same comparison through
    # the other entrypoint; it must agree with the gate.
    baseline, _ = gate
    assert (
        metrics_cli.main(
            ["--labeled-set", "eval/labeled-qa.json", "--baseline", str(baseline), "--record"]
        )
        == 0
    )

    assert (
        metrics_cli.main(
            ["--labeled-set", "eval/labeled-qa.json", "--baseline", str(baseline), "--check"]
        )
        == 0
    )
    assert "sin regresiones" in capsys.readouterr().out
