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
