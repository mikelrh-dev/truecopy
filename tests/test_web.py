"""Hermetic web-layer tests: auth, owner gate, and Spanish/English UI split.

These tests do not boot Streamlit. They exercise ``web/auth`` directly and
assert the Spanish UI strings the app is supposed to render, plus the English
manual-content contract (manual excerpts stay English; only chrome is Spanish).

Conventions:
* English code/comments, Spanish strings in ``web/ui_es`` and ``web/auth``.
* ``users.json``-backed stores always live in a disposable temp path provided by
  pytest ``tmp_path`` (never the real gitignored store).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from plant_rag import config
from web import auth
from web.ui_es import (
    ANSWER_LABEL,
    ANSWER_TIMEOUT_LABEL,
    APP_TITLE,
    LOGIN_ERROR_EMPTY_CODE,
    LOGIN_ERROR_EMPTY_PASSWORD,
    LOGIN_ERROR_GENERIC,
    LOGIN_SUBMIT,
    LOGIN_TITLE,
    OWNER_CODES_LABEL,
    OWNER_IS_OPERATOR,
    OWNER_IS_OWNER,
    OWNER_LOGOUT,
    QUESTION_SEND,
    QUESTION_PLACEHOLDER,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def make_store(tmp_path: Path) -> auth.AuthStore:
    return auth.AuthStore(tmp_path / "users.json")


def add_user(store: auth.AuthStore, code: str, password: str, owner: bool = False) -> auth.User:
    return store.add_user(code, password, owner=owner)


# ---------------------------------------------------------------------------
# auth store: valid / invalid login, owner gate, user management
# ---------------------------------------------------------------------------


def test_authenticate_accepts_valid_credentials(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "secreto-123", owner=False)
    user = store.authenticate("op1", "secreto-123")
    assert user.code == "op1"
    assert user.owner is False


def test_authenticate_rejects_unknown_code(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "secreto-123", owner=False)
    with pytest.raises(auth.WrongPasswordError):
        store.authenticate("op2", "secreto-123")


def test_authenticate_rejects_wrong_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "secreto-123", owner=False)
    with pytest.raises(auth.WrongPasswordError):
        store.authenticate("op1", "secreto-456")


def test_authenticate_rejects_blank_code_and_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "secreto-123", owner=False)

    with pytest.raises(ValueError, match="código"):
        store.authenticate("   ", "secreto-123")
    with pytest.raises(ValueError, match="contraseña"):
        store.authenticate("op1", "   ")


def test_authenticate_errors_are_spanish(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="código"):
        store.authenticate("", "x")
    with pytest.raises(ValueError, match="contraseña"):
        store.authenticate("x", "")
    with pytest.raises(auth.WrongPasswordError, match="incorrectos"):
        store.authenticate("noexiste", "x")


def test_authenticate_same_error_for_unknown_code_and_wrong_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "secreto-123", owner=False)

    with pytest.raises(auth.WrongPasswordError, match="incorrectos"):
        store.authenticate("noexiste", "secreto-123")
    with pytest.raises(auth.WrongPasswordError, match="incorrectos"):
        store.authenticate("op1", "secreto-456")


def test_owner_lookup_returns_user_only_for_owner(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "owner-a", "p", owner=True)
    add_user(store, "op-b", "p", owner=False)

    assert store.owner("owner-a") is not None
    assert store.owner("op-b") is None
    assert store.owner("noexiste") is None


def test_owner_can_manage_codes_from_ui_contract(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    owner = add_user(store, "owner", "pw", owner=True)
    assert store.owner(owner.code) is not None


def test_non_owner_cannot_manage_codes_from_ui_contract(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "owner", "pw", owner=True)
    add_user(store, "operador", "pw", owner=False)

    assert store.owner("owner") is not None
    assert store.owner("operador") is None


def test_add_user_rejects_blank_inputs_and_duplicates(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.add_user("existing", "pw", owner=False)

    with pytest.raises(ValueError, match="código"):
        store.add_user("", "pw")
    with pytest.raises(ValueError, match="contraseña"):
        store.add_user("nuevo", "")
    with pytest.raises(ValueError, match="Ya existe"):
        store.add_user("existing", "otra")


def test_remove_user_rejects_missing_user(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with pytest.raises(ValueError, match="No existe"):
        store.remove_user("noexiste")


def test_users_file_is_written_atomically(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "p", owner=False)

    users_path = tmp_path / "users.json"
    assert users_path.exists()
    decoded = users_path.read_text(encoding="utf-8")
    assert "op1" in decoded
    assert "secreto" not in decoded  # plaintext password never persisted


def test_users_file_never_contains_plaintext_password(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add_user(store, "op1", "mi-contraseña-secreta", owner=False)

    users_path = tmp_path / "users.json"
    decoded = users_path.read_text(encoding="utf-8")
    assert "mi-contraseña-secreta" not in decoded
    assert "$argon2" in decoded


def test_corrupt_users_file_raises_on_read(tmp_path: Path) -> None:
    users_path = tmp_path / "users.json"
    users_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupto"):
        auth.AuthStore(users_path)


def test_default_users_creates_owner_on_first_boot(tmp_path: Path) -> None:
    users_path = tmp_path / "users.json"
    store = auth.create_default_users(users_path)
    assert store.has_user("admin")
    assert store.owner("admin") is not None


def test_default_users_does_not_overwrite_existing_users(tmp_path: Path) -> None:
    users_path = tmp_path / "users.json"
    store = auth.AuthStore(users_path)
    store.add_user("existing", "pw", owner=False)
    auth.create_default_users(users_path)
    assert store.list_codes() == ["existing"]


# ---------------------------------------------------------------------------
# Spanish UI strings: chrome is Spanish
# ---------------------------------------------------------------------------


def test_login_chrome_is_spanish() -> None:
    assert LOGIN_TITLE
    assert LOGIN_SUBMIT
    assert LOGIN_ERROR_GENERIC
    assert LOGIN_ERROR_EMPTY_CODE
    assert LOGIN_ERROR_EMPTY_PASSWORD


def test_error_messages_are_spanish() -> None:
    assert "incorrectos" in LOGIN_ERROR_GENERIC
    assert "código" in LOGIN_ERROR_EMPTY_CODE
    assert "contraseña" in LOGIN_ERROR_EMPTY_PASSWORD


def test_app_chrome_is_spanish() -> None:
    assert APP_TITLE
    assert QUESTION_SEND
    assert QUESTION_PLACEHOLDER
    assert OWNER_CODES_LABEL
    assert OWNER_IS_OWNER
    assert OWNER_IS_OPERATOR
    assert OWNER_LOGOUT
    assert ANSWER_LABEL
    assert ANSWER_TIMEOUT_LABEL


# ---------------------------------------------------------------------------
# Language split: UI chrome Spanish, manual excerpts stay English
# ---------------------------------------------------------------------------

def _spanish_strings() -> list[str]:
    """All operator-facing strings the web layer owns, for a language audit."""
    return [
        LOGIN_TITLE,
        LOGIN_ERROR_GENERIC,
        LOGIN_ERROR_EMPTY_CODE,
        LOGIN_ERROR_EMPTY_PASSWORD,
        APP_TITLE,
        QUESTION_PLACEHOLDER,
        QUESTION_SEND,
        OWNER_CODES_LABEL,
        OWNER_IS_OWNER,
        OWNER_IS_OPERATOR,
        OWNER_LOGOUT,
        ANSWER_LABEL,
        ANSWER_TIMEOUT_LABEL,
    ]


def test_spanish_ui_strings_are_actually_spanish() -> None:
    # Practical language guard: the UI chrome the operator reads must not be
    # English operator dialogue. We do not demand accents/ñ on every string,
    # because legitimate Spanish UI uses plain verbs (e.g. "Preguntar").
    english_ui_words = re.compile(
        r"\b(Log in|Sign in|Sign up|Submit|Save|Cancel|Delete|Edit|Add|Remove|"
        r"Profile|Settings|Logout|Login|Username|Password|Search|Filter|Sort|"
        r"Owner|Operator|Admin|User|Question|Answer|Alert|Error|Success|OK|Yes|No)\b"
    )
    for text in _spanish_strings():
        assert isinstance(text, str), f"{text!r} must be a string"
        assert text.strip(), f"{text!r} must not be empty"
        assert not english_ui_words.search(text), (
            f"UI chrome {text!r} looks like English operator dialogue."
        )


def test_manual_excerpt_placeholder_is_spanish_question_prompt() -> None:
    # The placeholder for the operator question box is Spanish, while the
    # quoted manual content shown in answers remains English (language split).
    assert QUESTION_PLACEHOLDER
    assert "pregunta" in QUESTION_PLACEHOLDER.lower()


# ---------------------------------------------------------------------------
# upload staging: gitignored directory, never the repository root (verify W7)
# ---------------------------------------------------------------------------


def test_staged_uploads_land_in_a_gitignored_staging_directory(
    tmp_path: Path,
) -> None:
    from web.app import _stage_upload

    staged = _stage_upload(b"%PDF-1.7 fake", directory=tmp_path)

    assert staged.parent == tmp_path
    assert staged.name.endswith(".pdf")
    assert len(staged.name) == 36  # 32 hex chars + ".pdf": a UUID, not a filename
    assert staged.read_bytes() == b"%PDF-1.7 fake"


def test_staged_upload_default_directory_is_not_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.app import _stage_upload

    monkeypatch.chdir(tmp_path)
    staged = _stage_upload(b"%PDF-1.7 fake")  # no directory argument

    # The default is a dedicated staging dir, never the repository root.
    assert staged.parent == tmp_path / ".uploads"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".uploads"]

    # And both secrets/staging paths are actually gitignored in this repo.
    repo_gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    ignore = repo_gitignore.read_text(encoding="utf-8")
    assert ".uploads/" in ignore
    assert ".streamlit-users.json" in ignore


# ---------------------------------------------------------------------------
# the "in progress" flag must never get stuck
# ---------------------------------------------------------------------------


def test_a_fresh_busy_flag_blocks_and_a_stale_one_self_heals() -> None:
    from web import app as web_app

    state: dict = {}
    web_app._mark_busy(state)
    assert web_app._is_busy(state) is True

    # A rerun that died mid-question (refresh, dropped socket) never cleared
    # the flag. After the deadline plus margin it is stale by definition.
    future = state[web_app.BUSY_KEY] + (
        config.QUERY_DEADLINE_S + web_app._BUSY_STALE_MARGIN_S + 1.0
    )
    assert web_app._is_busy(state, now=future) is False
    assert web_app.BUSY_KEY not in state  # healed, not just ignored


def test_busy_flag_inside_the_deadline_still_blocks() -> None:
    from web import app as web_app

    state: dict = {}
    web_app._mark_busy(state)
    soon = state[web_app.BUSY_KEY] + (config.QUERY_DEADLINE_S - 5.0)

    assert web_app._is_busy(state, now=soon) is True  # genuinely running


def test_clearing_the_flag_frees_the_ui() -> None:
    from web import app as web_app

    state: dict = {}
    web_app._mark_busy(state)
    web_app._clear_busy(state)

    assert web_app._is_busy(state) is False


def test_answer_cache_is_resolved_once_per_process_like_the_query_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from plant_rag.answer.cache import AnswerCache
    from web import app as web_app

    calls: list[int] = []
    fake = AnswerCache(tmp_path / "cache.jsonl")
    monkeypatch.setattr(AnswerCache, "from_config", lambda: (calls.append(1) or fake))
    monkeypatch.setattr(web_app, "_ANSWER_CACHE", None)
    monkeypatch.setattr(web_app, "_ANSWER_CACHE_RESOLVED", False)

    first = web_app._answer_cache()
    second = web_app._answer_cache()

    assert first is fake and second is fake
    assert calls == [1]  # resolved once per process, reused afterwards


class _FakeAlias:
    def __init__(self, alias_name: str, collection_name: str) -> None:
        self.alias_name = alias_name
        self.collection_name = collection_name


class _FakeAliases:
    def __init__(self, aliases: list[_FakeAlias]) -> None:
        self.aliases = aliases


class _FakePoint:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeScrollClient:
    """Qdrant double: one current_text alias, two stored pages."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.scroll_calls = 0

    def get_aliases(self) -> _FakeAliases:
        return _FakeAliases([_FakeAlias("current_text", "manual_r6_text")])

    def scroll(self, **kwargs):
        self.scroll_calls += 1
        return [_FakePoint(page) for page in self._pages], None


def test_cited_excerpts_fetch_the_passages_behind_each_citation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from web import app as web_app

    client = _FakeScrollClient(
        [
            {"manual_id": "Owners_Manual", "page": 98, "parent_text": "Tire pressure 42 psi."},
            {"manual_id": "Owners_Manual", "page": 39, "parent_text": "Fasten the belt."},
        ]
    )
    # _cited_excerpts imports QdrantClient inside the function, so the
    # module-level patch is what the call resolves.
    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda *a, **kw: client, raising=True
    )

    from plant_rag.models import Answer, Citation

    answer = Answer(
        kind="answer",
        text_es="...",
        citations=(Citation("Owners_Manual", 98), Citation("Owners_Manual", 39)),
    )

    excerpts = web_app._cited_excerpts(answer.citations)

    assert excerpts[("Owners_Manual", 98)] == "Tire pressure 42 psi."
    assert excerpts[("Owners_Manual", 39)] == "Fasten the belt."
    assert client.scroll_calls == 1  # one scroll serves every citation


def test_cited_excerpts_fail_open_when_the_corpus_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from web import app as web_app

    class _Broken:
        def get_aliases(self):
            raise RuntimeError("qdrant down")

    monkeypatch.setattr(
        "qdrant_client.QdrantClient", lambda *a, **kw: _Broken(), raising=True
    )
    from plant_rag.models import Answer, Citation

    answer = Answer(
        kind="answer",
        text_es="...",
        citations=(Citation("Owners_Manual", 98),),
    )

    # An excerpt is a trust aid: never a reason to withhold the answer.
    assert web_app._cited_excerpts(answer.citations) == {}


# ---------------------------------------------------------------------------
# refusal display: the UI shows the exact contract string, never a paraphrase
# ---------------------------------------------------------------------------


def test_there_is_no_second_wording_for_the_refusal_in_the_ui() -> None:
    # CLI, eval and tests assert exactly REFUSAL_ES ("No aparece en la
    # documentación"). The web once rendered its own paraphrase ("No se
    # encontró una respuesta en el manual."), forking the contract: two
    # wordings for the same event in web vs CLI.
    from web import ui_es

    assert not hasattr(ui_es, "ANSWER_UNASSIGNED")
    web_root = Path(__file__).resolve().parents[1] / "web"
    for module in ("app.py", "ui_es.py"):
        source = (web_root / module).read_text(encoding="utf-8")
        assert "No se encontró una respuesta en el manual" not in source


def test_the_refusal_render_path_uses_the_answer_text() -> None:
    # The web renders answer.text_es for refusals: the pipeline builds refusal
    # Answers with exactly REFUSAL_ES by construction, so what the operator
    # reads is always the contract string itself.
    import inspect

    from web import app as web_app

    rendered = inspect.getsource(web_app._render_answer)
    assert "answer.text_es" in rendered
    assert "ANSWER_UNASSIGNED" not in rendered
