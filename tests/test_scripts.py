"""Entrypoint smoke tests for the documented CLI scripts (verify remediation C1, S1).

No test ever imported ``scripts/``, so a broken entrypoint (``ModuleNotFoundError``,
wrong repo root) passed the suite silently while the README runbook failed with
exit 1. These tests run every documented entrypoint the way an operator runs it —
from the repo root, with the active interpreter — plus the README import
verification command, so that class of breakage cannot regress unnoticed.

Conventions: English code, hermetic subprocesses (no Qdrant/Ollama required —
``--help`` and imports must work with services down).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from plant_rag.models import Answer

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
ENTRYPOINTS = ("ingest", "reindex", "query", "eval", "run_web")


def _run(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.parametrize("name", ENTRYPOINTS)
def test_script_entrypoint_help_exits_zero(name: str) -> None:
    """Every documented entrypoint imports its dependencies and parses args."""
    result = _run([sys.executable, str(SCRIPTS_DIR / f"{name}.py"), "--help"])
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert "usage:" in result.stdout.lower()


def test_readme_import_verification_command_runs() -> None:
    # The exact command in README "Verify the core imports without services".
    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "import plant_rag.config, plant_rag.models"
    )
    result = _run([sys.executable, "-c", code])
    assert result.returncode == 0, f"stderr:\n{result.stderr}"


def test_run_web_repo_root_resolves_web_and_src() -> None:
    from scripts import run_web

    repo = run_web._repo_root()
    assert repo == REPO_ROOT
    assert (repo / "web" / "app.py").is_file()
    assert (repo / "src" / "plant_rag" / "__init__.py").is_file()


def test_run_web_env_puts_repo_and_src_on_pythonpath() -> None:
    from scripts import run_web

    repo = run_web._repo_root()
    env = run_web.build_env(repo, {"PYTHONPATH": r"C:\pre\existing", "PATH": "x"})

    entries = env["PYTHONPATH"].split(os.pathsep)
    assert str(repo) in entries
    assert str(repo / "src") in entries
    assert r"C:\pre\existing" in entries
    assert env["PATH"] == "x"


def test_run_web_builds_streamlit_command_for_the_repo_app() -> None:
    from scripts import run_web

    repo = run_web._repo_root()
    cmd = run_web.build_command("streamlit", repo / "web" / "app.py", "0.0.0.0", 8501)

    assert cmd[:2] == ["streamlit", "run"]
    assert str(repo / "web" / "app.py") in cmd
    assert "--server.address=0.0.0.0" in cmd
    assert "--server.port=8501" in cmd
    assert "--server.headless=true" in cmd


def test_run_web_finds_streamlit_without_an_activated_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """README flow: ``.venv/bin/python scripts/run_web.py`` with no activation.

    ``shutil.which`` alone fails there (PATH has no ``Scripts/``), so the
    launcher must fall back to the interpreter's own environment.
    """
    from scripts import run_web

    monkeypatch.setattr(run_web.shutil, "which", lambda name: None)
    resolved = run_web.resolve_streamlit()

    assert resolved is not None
    path = Path(resolved)
    assert path.exists()
    assert "streamlit" in path.name.lower()
    assert path.parent == Path(sys.executable).resolve().parent


# ---------------------------------------------------------------------------
# W2: the query CLI must share the exact-refusal contract with models
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Dimension-correct dummy so the CLI's empty-hit path stays hermetic."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        from plant_rag import config

        return [[0.0] * config.EMBED_DIMS for _ in texts]


def test_query_cli_empty_alarm_result_returns_the_shared_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.query as query_cli
    from plant_rag.models import REFUSAL_ES

    monkeypatch.setattr(
        query_cli,
        "answer_question",
        lambda question, remaining_s, **kw: Answer(kind="refusal", text_es=REFUSAL_ES, citations=()),
    )
    answer = query_cli._answer_to_cli(
        query_cli.answer_question("99999", remaining_s=10.0)
    )

    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == []


def test_query_cli_empty_semantic_result_returns_the_shared_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.query as query_cli
    from plant_rag.models import REFUSAL_ES

    monkeypatch.setattr(
        query_cli,
        "answer_question",
        lambda question, remaining_s, **kw: Answer(kind="refusal", text_es=REFUSAL_ES, citations=()),
    )
    answer = query_cli._answer_to_cli(
        query_cli.answer_question("¿qué es el bypass?", remaining_s=10.0)
    )

    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == []


def test_query_cli_generate_passes_the_configured_answer_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI is operator traffic: its generator wires the cache seam."""
    import scripts.query as query_cli
    from plant_rag.answer.cache import AnswerCache
    from plant_rag.models import REFUSAL_ES

    sentinel = object()
    monkeypatch.setattr(AnswerCache, "from_config", lambda: sentinel)
    seen: dict = {}

    def _fake_answer(question, remaining_s, **kw):
        seen.update(kw)
        return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())

    monkeypatch.setattr(query_cli, "answer_question", _fake_answer)
    query_cli._generate("hola", 10.0)

    assert seen["cache"] is sentinel


# ---------------------------------------------------------------------------
# Phase 3: per-manual calibration records (--record on the eval CLI)
# ---------------------------------------------------------------------------


def _patch_eval_harness(
    monkeypatch: pytest.MonkeyPatch,
    cal_path: Path | str,
    *,
    break_check: str | None = None,
) -> object:
    """Replace every live dependency of ``eval.main`` with hermetic fakes.

    ``break_check`` names one of the four checks to fail, exercising the
    "record only after ALL checks pass" gate. Returns the fixed report the
    fake sweep hands back.
    """
    import scripts.eval as eval_cli
    from scripts.eval import CalibrationPoint, CalibrationReport, EvalResult
    from plant_rag import config

    monkeypatch.setattr(
        config, "CALIBRATION_PATH", str(cal_path) if cal_path else ""
    )
    monkeypatch.setattr(config, "MIN_SCORE", 0.40)

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def get_collections(self):
            return None

    monkeypatch.setattr(eval_cli, "QdrantClient", _FakeClient)
    monkeypatch.setattr(eval_cli, "_has_ollama", lambda: True)
    monkeypatch.setattr(
        eval_cli,
        "current_manual_id",
        lambda client: "828D_DA_1015_en_en_US",
    )
    monkeypatch.setattr(eval_cli, "current_revision", lambda client: 3)

    def _result(name: str, passed: bool, **fields: object) -> EvalResult:
        return EvalResult(
            name=name, passed=passed, latency_s=0.0, detail="ok", **fields
        )

    checks = (
        ("alarm", "check_alarm_known_and_unknown", "alarm retrieval"),
        ("crosslingual", "check_crosslingual_es_en", "cross-lingual ES->EN"),
        ("refusal", "check_refusal_out_of_corpus", "refusal"),
        ("golden", "check_golden_grounding", "golden grounding"),
    )
    for key, attr, name in checks:
        passed = break_check != key
        fields: dict = {}
        if key == "golden" and passed:
            fields = {"golden_pass": 2, "golden_total": 2}
        monkeypatch.setattr(
            eval_cli,
            attr,
            lambda client, *a, _name=name, _passed=passed, _fields=fields: _result(
                _name, _passed, **_fields
            ),
        )

    report = CalibrationReport(
        points=[
            CalibrationPoint("threshold=0.30", 0.30, 5, 1),
            CalibrationPoint("threshold=0.50", 0.50, 5, 0),
            CalibrationPoint("threshold=0.60", 0.60, 4, 0),
        ],
        in_total=5,
        out_total=5,
    )
    monkeypatch.setattr(eval_cli, "run_calibration", lambda client: report)
    return report


def test_eval_without_record_writes_no_file(tmp_path, monkeypatch) -> None:
    """The default run keeps its old contract: no store, no side effects."""
    import scripts.eval as eval_cli

    cal_path = tmp_path / "calibration.jsonl"
    _patch_eval_harness(monkeypatch, cal_path)

    assert eval_cli.main([]) == 0
    assert not cal_path.exists()


def test_eval_record_appends_one_line_and_keeps_output_and_min_score(
    tmp_path, monkeypatch, capsys
) -> None:
    """``--record`` appends exactly one v1 line, silently, after a pass."""
    import scripts.eval as eval_cli
    from plant_rag import config

    cal_path = tmp_path / "nested" / "calibration.jsonl"  # also proves mkdir -p
    _patch_eval_harness(monkeypatch, cal_path)

    assert eval_cli.main(["--record"]) == 0

    out = capsys.readouterr().out
    assert "Recommendation: PLANT_RAG_MIN_SCORE=0.50" in out  # report line stays
    assert "Sugerencia de calibración" not in out  # recording adds no line
    assert "Aviso [" not in out
    assert config.MIN_SCORE == 0.40  # the sweep never rewrites the default

    lines = cal_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["manual_id"] == "828D_DA_1015_en_en_US"
    assert rec["revision"] == "r3"
    assert rec["recommended_min_score"] == 0.5
    assert rec["current_min_score"] == 0.4
    assert (rec["in_corpus_hits"], rec["in_total"]) == (5, 5)
    assert (rec["out_of_corpus_hits"], rec["out_total"]) == (0, 5)
    assert rec["golden_pass"] == 2 and rec["golden_total"] == 2
    assert rec["golden_set_present"] is True
    assert rec["separates_labeled_set"] is True
    assert rec["source"] == "eval"
    assert "schema" not in rec  # v1 keys stay exactly the design set
    assert rec["sweep"] == [
        {"threshold": 0.3, "in_hits": 5, "out_hits": 1},
        {"threshold": 0.5, "in_hits": 5, "out_hits": 0},
        {"threshold": 0.6, "in_hits": 4, "out_hits": 0},
    ]
    assert rec["recorded_at"]


def test_eval_record_conflicts_with_no_calibration_and_exits_two() -> None:
    """The two modes are mutually exclusive: argparse rejects the combo."""
    import scripts.eval as eval_cli

    with pytest.raises(SystemExit) as excinfo:
        eval_cli.main(["--record", "--no-calibration"])

    assert excinfo.value.code == 2


def test_eval_record_with_empty_path_is_silent_and_exit_unchanged(
    tmp_path, monkeypatch, capsys
) -> None:
    """``CALIBRATION_PATH=""`` disables recording without any behavior change."""
    import scripts.eval as eval_cli

    _patch_eval_harness(monkeypatch, "")

    assert eval_cli.main(["--record"]) == 0
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere
    out = capsys.readouterr().out
    assert "Recommendation: PLANT_RAG_MIN_SCORE=0.50" in out
    assert "Sugerencia de calibración" not in out


def test_eval_record_never_appends_when_any_check_fails(tmp_path, monkeypatch) -> None:
    """A failing mandatory check keeps exit 1 AND skips the record."""
    import scripts.eval as eval_cli

    cal_path = tmp_path / "calibration.jsonl"
    _patch_eval_harness(monkeypatch, cal_path, break_check="refusal")

    assert eval_cli.main(["--record"]) == 1
    assert not cal_path.exists()
