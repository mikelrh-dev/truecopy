"""WA-R3 covering checks: owner self-operation from the UI (verify finding C3).

No test ever executed ``web/app.py``, so the owner scenario "Owner uploads and
reindexes" shipped untested — the app had undefined UI names, a wrong login
label, and no dispatch, all invisible to the suite. These checks pin:

- upload validation (extension / MIME / nothing-selected) and the UUID rename,
- that a valid upload STARTS ingestion with Spanish progress/status,
- the reindex handoff to the now-working ``scripts/reindex.py``,
- owner-only gating of the operations sidebar and code management,
- login gating (valid credentials open the app; invalid ones show the
  Spanish error and grant no access),
- the CLI reindex path itself.

Qdrant, Ollama, and the embedding model are stubbed so the suite stays
hermetic. AppTest cannot drive ``st.file_uploader``, so the upload pipeline is
exercised at function level with a fake upload widget value; the widget layer's
"nothing selected" branch runs in AppTest.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

import plant_rag.ingest.reindex as reindex_mod
import plant_rag.retrieve.embedder as embedder_mod
import qdrant_client
import web.app as webapp
from plant_rag import config
from plant_rag.models import REFUSAL_ES, Answer, Citation
from web import auth
from web.ui_es import (
    FEEDBACK_DONE,
    FEEDBACK_THUMBS_DOWN,
    FEEDBACK_THUMBS_UP,
    LOGIN_CODE_LABEL,
    LOGIN_ERROR_GENERIC,
    LOGIN_PASSWORD_LABEL,
    LOGIN_SUBMIT,
    OWNER_CODE_ADD,
    OWNER_CODES_LABEL,
    OWNER_REINDEX_LABEL,
    OWNER_TAB_TITLE,
    OWNER_UPLOAD_BAD_EXT,
    OWNER_UPLOAD_BAD_MIME,
    OWNER_UPLOAD_DONE,
    OWNER_UPLOAD_EMPTY,
    OWNER_UPLOAD_FAILED,
    OWNER_UPLOAD_IN_PROGRESS,
    OWNER_UPLOAD_LABEL,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "web" / "app.py"


class _FakeUpload:
    """Stand-in for the object ``st.file_uploader`` yields on success."""

    def __init__(
        self,
        name: str = "manual.pdf",
        mime: str | None = "application/pdf",
        data: bytes = b"%PDF-1.4 fake",
    ) -> None:
        self.name = name
        self.type = mime
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


@pytest.fixture()
def store(tmp_path: Path) -> auth.AuthStore:
    auth_store = auth.AuthStore(tmp_path / "users.json")
    auth_store.add_user("admin", "pw-owner", owner=True)
    auth_store.add_user("op1", "pw-op", owner=False)
    return auth_store


def _app(store: auth.AuthStore) -> AppTest:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    at.session_state["auth_store"] = store
    return at


def _login(store: auth.AuthStore, code: str, password: str) -> AppTest:
    at = _app(store)
    at.run()
    at.text_input(key="login_code").set_value(code)
    at.text_input(key="login_password").set_value(password)
    next(b for b in at.button if b.label == LOGIN_SUBMIT).click()
    at.run()
    return at


def _stub_st(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Replace ``web.app.st`` with a mock whose ``status`` supports ``with``."""
    st = MagicMock()
    status = MagicMock()
    st.status.return_value.__enter__.return_value = status
    monkeypatch.setattr(webapp, "st", st)
    return st, status


# ---------------------------------------------------------------------------
# upload validation and the UUID rename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        ("manual.pdf", "application/pdf", None),
        ("manual.PDF", "application/pdf", None),
        ("manual.txt", "text/plain", OWNER_UPLOAD_BAD_EXT),
        ("manual.pdf", "text/plain", OWNER_UPLOAD_BAD_MIME),
        ("manual.pdf", None, OWNER_UPLOAD_BAD_MIME),
    ],
)
def test_upload_rejection_rules(name: str, mime: str | None, expected: str | None) -> None:
    assert webapp._upload_rejection(name, mime) == expected


def test_stage_upload_renames_to_a_uuid_pdf(tmp_path: Path) -> None:
    staged = webapp._stage_upload(b"%PDF-1.4 payload", directory=tmp_path)

    assert staged.parent == tmp_path
    assert re.fullmatch(r"[0-9a-f]{32}\.pdf", staged.name)
    assert staged.read_bytes() == b"%PDF-1.4 payload"

    other = webapp._stage_upload(b"%PDF-1.4 payload", directory=tmp_path)
    assert other != staged  # fresh UUID per upload


def test_upload_button_without_file_shows_spanish_error(store: auth.AuthStore) -> None:
    at = _login(store, "admin", "pw-owner")
    assert not at.exception

    at.button(key="owner_upload_btn").click()
    at.run()

    assert not at.exception
    assert OWNER_UPLOAD_EMPTY in [e.value for e in at.error]


# ---------------------------------------------------------------------------
# WA-R3: a valid upload starts ingestion with Spanish progress/status
# ---------------------------------------------------------------------------


def test_owner_upload_starts_ingestion_with_spanish_progress(
    store: auth.AuthStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_stage = webapp._stage_upload
    monkeypatch.setattr(webapp, "_stage_upload", lambda data: real_stage(data, tmp_path))
    st, status = _stub_st(monkeypatch)
    run_reindex = MagicMock(return_value=SimpleNamespace(revision=7))
    monkeypatch.setattr(reindex_mod, "run_reindex", run_reindex)
    monkeypatch.setattr(qdrant_client, "QdrantClient", MagicMock())
    monkeypatch.setattr(embedder_mod, "get_embedder", MagicMock())

    webapp._do_upload(_FakeUpload())

    assert run_reindex.call_count == 1
    args, kwargs = run_reindex.call_args
    staged_path = Path(args[0])
    assert staged_path.parent == tmp_path
    assert staged_path.suffix == ".pdf"
    # The manual's own filename is the identity (audit A1): the same
    # namespace the CLI uses, so goldens and calibration records match.
    assert kwargs["manual_id"] == "manual"

    assert st.status.call_args.args[0] == OWNER_UPLOAD_IN_PROGRESS
    status.update.assert_called_once()
    assert OWNER_UPLOAD_DONE in status.update.call_args.kwargs["label"]
    st.success.assert_called_once_with(OWNER_UPLOAD_DONE)


def test_two_different_uploads_get_two_different_manual_ids(
    store: auth.AuthStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Identity is the manual's name, never one shared bucket (audit A1).

    With the old ``"manual-actual"`` id, a calibration record measured for
    one manual was served as the suggestion for a different one.
    """
    real_stage = webapp._stage_upload
    monkeypatch.setattr(webapp, "_stage_upload", lambda data: real_stage(data, tmp_path))
    st, _ = _stub_st(monkeypatch)
    run_reindex = MagicMock(return_value=SimpleNamespace(revision=7))
    monkeypatch.setattr(reindex_mod, "run_reindex", run_reindex)
    monkeypatch.setattr(qdrant_client, "QdrantClient", MagicMock())
    monkeypatch.setattr(embedder_mod, "get_embedder", MagicMock())

    webapp._do_upload(_FakeUpload(name="manual de calidad.pdf"))
    webapp._do_upload(_FakeUpload(name="manual de seguridad.pdf"))

    ids = [call.kwargs["manual_id"] for call in run_reindex.call_args_list]
    assert ids == ["manual_de_calidad", "manual_de_seguridad"]


def test_the_uploaded_filename_never_reaches_the_staged_path(
    store: auth.AuthStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """UUID staging still holds: the name carries identity, not the path."""
    real_stage = webapp._stage_upload
    monkeypatch.setattr(webapp, "_stage_upload", lambda data: real_stage(data, tmp_path))
    st, _ = _stub_st(monkeypatch)
    run_reindex = MagicMock(return_value=SimpleNamespace(revision=7))
    monkeypatch.setattr(reindex_mod, "run_reindex", run_reindex)
    monkeypatch.setattr(qdrant_client, "QdrantClient", MagicMock())
    monkeypatch.setattr(embedder_mod, "get_embedder", MagicMock())

    webapp._do_upload(_FakeUpload(name="../../etc/passwd.pdf"))

    staged = Path(run_reindex.call_args.args[0])
    assert staged.parent == tmp_path  # traversal never escapes staging
    assert "passwd" in run_reindex.call_args.kwargs["manual_id"]


def test_owner_upload_failure_reports_the_spanish_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real_stage = webapp._stage_upload
    monkeypatch.setattr(webapp, "_stage_upload", lambda data: real_stage(data, tmp_path))
    st, _ = _stub_st(monkeypatch)
    monkeypatch.setattr(
        reindex_mod, "run_reindex", MagicMock(side_effect=RuntimeError("qdrant down"))
    )
    monkeypatch.setattr(qdrant_client, "QdrantClient", MagicMock())
    monkeypatch.setattr(embedder_mod, "get_embedder", MagicMock())

    webapp._do_upload(_FakeUpload())

    (message,) = [c.args[0] for c in st.error.call_args_list]
    assert message.startswith(OWNER_UPLOAD_FAILED)
    assert "qdrant down" in message


# ---------------------------------------------------------------------------
# reindex trigger: web handoff + the CLI path it points to
# ---------------------------------------------------------------------------


def test_reindex_button_hands_off_to_the_cli_with_spanish_guidance(
    store: auth.AuthStore,
) -> None:
    at = _login(store, "admin", "pw-owner")
    assert not at.exception

    with (
        patch.object(qdrant_client, "QdrantClient", MagicMock()),
        patch.object(embedder_mod, "get_embedder", MagicMock()),
        patch.object(reindex_mod, "run_reindex", MagicMock()) as run_reindex,
    ):
        at.button(key="owner_reindex_btn").click()
        at.run()

    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("scripts/reindex.py" in w for w in warnings)
    # Manual-only reindex has no stored PDF path: the web button guides to the
    # CLI instead of silently ingesting; the CLI path itself is exercised below.
    run_reindex.assert_not_called()


def test_cli_reindex_reports_a_missing_pdf_with_exit_one() -> None:
    missing = REPO_ROOT / "no-such-manual.pdf"
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "reindex.py"), str(missing)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 1
    assert "replacement PDF not found" in result.stdout + result.stderr


# ---------------------------------------------------------------------------
# login gating and owner-only sidebar
# ---------------------------------------------------------------------------


def test_login_form_uses_the_operator_code_label(store: auth.AuthStore) -> None:
    at = _app(store)
    at.run()

    assert not at.exception
    labels = {t.key: t.label for t in at.text_input}
    assert labels["login_code"] == LOGIN_CODE_LABEL
    assert labels["login_password"] == LOGIN_PASSWORD_LABEL
    assert labels["login_code"] != labels["login_password"]


def test_valid_credentials_open_the_app(store: auth.AuthStore) -> None:
    at = _login(store, "admin", "pw-owner")

    assert not at.exception
    assert at.session_state["current_user"].code == "admin"
    assert LOGIN_CODE_LABEL not in [t.label for t in at.text_input]


def test_invalid_credentials_show_the_spanish_error_and_grant_no_access(
    store: auth.AuthStore,
) -> None:
    at = _login(store, "admin", "wrong-password")

    assert not at.exception
    assert LOGIN_ERROR_GENERIC in [e.value for e in at.error]
    assert "current_user" not in at.session_state or at.session_state["current_user"] is None
    assert OWNER_TAB_TITLE not in [h.value for h in at.sidebar.header]


def test_owner_sees_the_owner_operations(store: auth.AuthStore) -> None:
    at = _login(store, "admin", "pw-owner")

    assert not at.exception
    assert OWNER_TAB_TITLE in [h.value for h in at.sidebar.header]
    button_labels = [b.label for b in at.button]
    assert OWNER_UPLOAD_LABEL in button_labels
    assert OWNER_REINDEX_LABEL in button_labels
    assert OWNER_CODE_ADD in button_labels


def test_operator_is_gated_out_of_owner_operations(store: auth.AuthStore) -> None:
    at = _login(store, "op1", "pw-op")

    assert not at.exception
    assert OWNER_TAB_TITLE not in [h.value for h in at.sidebar.header]
    button_labels = [b.label for b in at.button]
    assert OWNER_UPLOAD_LABEL not in button_labels
    assert OWNER_REINDEX_LABEL not in button_labels
    assert OWNER_CODE_ADD not in button_labels


# ---------------------------------------------------------------------------
# operator code management
# ---------------------------------------------------------------------------


def test_owner_can_add_an_operator_code(store: auth.AuthStore) -> None:
    at = _login(store, "admin", "pw-owner")
    assert not at.exception
    assert OWNER_CODES_LABEL in [s.value for s in at.subheader]

    at.text_input(key="owner_code_new_code").set_value("op9")
    at.text_input(key="owner_code_new_password").set_value("pw-nine")
    at.button(key="owner_code_add_btn").click()
    at.run()

    assert not at.exception
    assert store.has_user("op9")


# ---------------------------------------------------------------------------
# thumbs widget: one vote per shown answer (operator-feedback spec)
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _seed_last_answer(at: AppTest, question: str, *, kind: str = "answer") -> AppTest:
    citations = (Citation("Owners_Manual", 98),) if kind == "answer" else ()
    at.session_state["last_answer"] = Answer(
        kind=kind,
        text_es="La presión de servicio es 2.4 bar." if kind == "answer" else "No aparece en la documentación",
        citations=citations,
    )
    at.session_state["last_answer_question"] = question
    at.run()
    return at


def test_vote_is_recorded_once_and_then_the_control_disables(
    store: auth.AuthStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(config, "QUERY_LOG_PATH", str(log_path))
    at = _seed_last_answer(_login(store, "admin", "pw-owner"), "presion neumaticos")

    assert not at.exception
    assert FEEDBACK_THUMBS_UP in [b.label for b in at.button]
    assert FEEDBACK_THUMBS_DOWN in [b.label for b in at.button]

    next(b for b in at.button if b.label == FEEDBACK_THUMBS_UP).click()
    at.run()

    assert not at.exception
    votes = _read_jsonl(log_path)
    assert len(votes) == 1
    assert votes[0]["event"] == "feedback"
    assert votes[0]["vote"] == "up"
    assert votes[0]["question"] == "presion neumaticos"
    assert votes[0]["kind"] == "answer"
    # Confirmation replaces the control: no second vote is possible.
    assert FEEDBACK_DONE in [c.value for c in at.caption]
    assert FEEDBACK_THUMBS_UP not in [b.label for b in at.button]
    assert FEEDBACK_THUMBS_DOWN not in [b.label for b in at.button]

    at.run()  # a rerun alone never records a second vote
    assert len(_read_jsonl(log_path)) == 1


def test_a_timeout_notice_is_not_rateable(
    store: auth.AuthStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(config, "QUERY_LOG_PATH", str(log_path))
    at = _seed_last_answer(
        _login(store, "admin", "pw-owner"), "consulta lenta", kind="timeout"
    )

    assert not at.exception
    labels = [b.label for b in at.button]
    assert FEEDBACK_THUMBS_UP not in labels
    assert FEEDBACK_THUMBS_DOWN not in labels
    assert FEEDBACK_DONE not in [c.value for c in at.caption]
    assert _read_jsonl(log_path) == []


def test_a_new_question_resets_the_rating_state(
    store: auth.AuthStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plant_rag.answer import pipeline as pipeline_mod

    log_path = tmp_path / "queries.jsonl"
    monkeypatch.setattr(config, "QUERY_LOG_PATH", str(log_path))
    at = _seed_last_answer(_login(store, "admin", "pw-owner"), "presion neumaticos")

    next(b for b in at.button if b.label == FEEDBACK_THUMBS_UP).click()
    at.run()
    assert len(_read_jsonl(log_path)) == 1

    # Ask a NEW question: the chain runs through the shared pipeline seam,
    # stubbed here so the suite stays hermetic (no Qdrant, no Ollama).
    monkeypatch.setattr(
        pipeline_mod,
        "answer_question",
        lambda question, remaining_s, **kw: Answer(
            kind="refusal", text_es=REFUSAL_ES, citations=()
        ),
    )
    at.text_area(key="question_input").set_value("¿qué es el bypass?")
    at.button(key="ask_btn").click()
    at.run()

    assert not at.exception
    # The reset worked: this new rateable answer offers the control again.
    assert FEEDBACK_THUMBS_UP in [b.label for b in at.button]
    assert len(_read_jsonl(log_path)) == 1  # still only the first vote

    next(b for b in at.button if b.label == FEEDBACK_THUMBS_UP).click()
    at.run()

    assert not at.exception
    votes = _read_jsonl(log_path)
    assert len(votes) == 2  # a new answer accepts its own vote
    assert votes[1]["question"] == "¿qué es el bypass?"
    assert votes[1]["kind"] == "refusal"
