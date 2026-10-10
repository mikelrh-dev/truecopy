"""Streamlit LAN app: login gate, question queue, answer render, owner ops.

Conventions established here:

* The web layer is infrastructure: code and comments stay English, while every
  string the operator reads comes from ``web/ui_es.py`` (web-access-auth spec:
  "Spanish UI, English manual content").
* The answering contract is inviolable at the UI boundary too: grounded answers
  are shown with their citations, the exact ``REFUSAL_ES`` is shown as-is, the
  timeout notice is shown as-is, and a transport failure is rendered as a
  Spanish infrastructure message rather than a crash (answering spec: "never
  invent"; ADR-004; ADR-005).
* Owner privileges gate the upload / reindex / code-management UI. Anonymous
  users cannot reach those controls at all.
* Uploads are PDF-only and renamed with a UUID before anything touches ingestion
  modules. The app does not trust the browser-supplied filename.
* The scheduler is driven by the same call sequence the handoff specifies:
  ``submit() -> position``, then a worker loop calling ``run_once()`` until it
  returns ``None``. The web layer owns the loop for a single submitted question
  so the UI stays responsive while the answer is being produced.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import streamlit as st

from plant_rag import config
from plant_rag.answer.llm import LLMError
from plant_rag.models import Answer
from plant_rag.scheduling.scheduler import Scheduler, queue_position_es

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from plant_rag.monitoring import QueryLog

from web import auth
from web.ui_es import (
    APP_TITLE,
    LOGIN_CODE_LABEL,
    LOGIN_ERROR_EMPTY_CODE,
    LOGIN_ERROR_EMPTY_PASSWORD,
    LOGIN_ERROR_GENERIC,
    LOGIN_ERROR_STORE,
    LOGIN_PASSWORD_LABEL,
    LOGIN_SUBMIT,
    LOGIN_TITLE,
    PREWARM_LABEL,
    OWNER_CODE_ADD,
    OWNER_CODE_ADD_DONE,
    OWNER_CODE_ADD_DUPLICATE,
    OWNER_CODE_NEW_LABEL,
    OWNER_CODE_NEW_PASSWORD_LABEL,
    OWNER_CODE_OWNER_CANNOT_REMOVE,
    OWNER_CODE_REMOVE,
    OWNER_CODE_REMOVE_DONE,
    OWNER_CODE_REMOVE_MISSING,
    OWNER_CODES_HELP,
    OWNER_CODES_LABEL,
    OWNER_CURRENT_USER,
    OWNER_IS_OPERATOR,
    OWNER_IS_OWNER,
    OWNER_LOGOUT,
    OWNER_REINDEX_FAILED,
    OWNER_REINDEX_HELP,
    OWNER_REINDEX_IN_PROGRESS,
    OWNER_REINDEX_LABEL,
    OWNER_SWITCH_ACCOUNT,
    OWNER_TAB_TITLE,
    OWNER_UPLOAD_BAD_EXT,
    OWNER_UPLOAD_BAD_MIME,
    OWNER_UPLOAD_DONE,
    OWNER_UPLOAD_EMPTY,
    OWNER_UPLOAD_FAILED,
    OWNER_UPLOAD_HELP,
    OWNER_UPLOAD_IN_PROGRESS,
    OWNER_UPLOAD_LABEL,
    QUESTION_BUSY,
    QUESTION_EMPTY,
    QUESTION_PLACEHOLDER,
    QUESTION_SEND,
    QUEUE_POSITION_REFRESH,
    STATUS_OK,
    ANSWER_CITATIONS_LABEL,
    ANSWER_EXCERPT_LABEL,
    ANSWER_INFRA_ERROR_LABEL,
    ANSWER_INFRA_ERROR_PROMPT,
    ANSWER_LABEL,
    ANSWER_TIMEOUT_LABEL,
    ANSWER_TIMEOUT_PROMPT,
    FEEDBACK_DONE,
    FEEDBACK_LABEL,
    FEEDBACK_THUMBS_DOWN,
    FEEDBACK_THUMBS_UP,
)

# ---------------------------------------------------------------------------
# auth / session
# ---------------------------------------------------------------------------

AUTH_STORE_KEY = "auth_store"
CURRENT_USER_KEY = "current_user"


def _resolve_auth_store() -> auth.AuthStore:
    """Lazy auth store bound to a gitignored path, reused across reloads."""
    store = st.session_state.get(AUTH_STORE_KEY)
    if store is None:
        users_path = Path(".streamlit-users.json")
        store = auth.create_default_users(users_path)
        st.session_state[AUTH_STORE_KEY] = store
    return store


def _current_user() -> auth.User | None:
    return st.session_state.get(CURRENT_USER_KEY)


def _set_current_user(user: auth.User | None) -> None:
    if user is None:
        st.session_state.pop(CURRENT_USER_KEY, None)
    else:
        st.session_state[CURRENT_USER_KEY] = user


# ---------------------------------------------------------------------------
# answering wiring (the shared chain: plant_rag.answer.pipeline)
# ---------------------------------------------------------------------------


# The web layer used to carry its own copy of the alarm -> semantic -> LLM
# chain plus a verbatim copy of the Ollama transport. ``_scheduler`` now runs
# ``answer_question`` directly: ONE chain, ONE transport (llm.post_chat), so a
# contract fix lands everywhere at once.


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------


def _page_login() -> None:
    st.title(LOGIN_TITLE)

    st.text_input(LOGIN_CODE_LABEL, key="login_code")
    st.text_input(LOGIN_PASSWORD_LABEL, type="password", key="login_password")
    submit = st.button(LOGIN_SUBMIT, type="primary")

    if submit:
        store = _resolve_auth_store()
        raw_code = st.session_state.get("login_code", "")
        raw_password = st.session_state.get("login_password", "")

        if not raw_code.strip():
            st.error(LOGIN_ERROR_EMPTY_CODE)
        elif not raw_password.strip():
            st.error(LOGIN_ERROR_EMPTY_PASSWORD)
        else:
            try:
                user = store.authenticate(raw_code, raw_password)
                _set_current_user(user)
                st.success(STATUS_OK)
                st.rerun()
            except auth.WrongPasswordError:
                st.error(LOGIN_ERROR_GENERIC)
            except Exception:
                st.error(LOGIN_ERROR_STORE)


def _page_app() -> None:
    st.title(APP_TITLE)

    user = _current_user()
    if user is None:
        st.warning("Sesión no válida. Vuelva a iniciar sesión.")
        _set_current_user(None)
        st.rerun()

    _show_user_banner(user)

    if user.owner:
        _page_owner(user)

    _page_question()


def _show_user_banner(user: auth.User) -> None:
    role = OWNER_IS_OWNER if user.owner else OWNER_IS_OPERATOR
    st.write(f"{OWNER_CURRENT_USER}: **{user.code}** — {role}")
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button(OWNER_SWITCH_ACCOUNT, key="switch_account"):
            _set_current_user(None)
            st.rerun()
    with col2:
        st.button(OWNER_LOGOUT, key="logout", on_click=_set_current_user, args=(None,))


def _page_owner(user: auth.User) -> None:
    with st.sidebar:
        st.header(OWNER_TAB_TITLE)
        _owner_upload()
        st.divider()
        _owner_reindex()
        st.divider()
        _owner_codes(user)


def _upload_rejection(name: str, mime: str | None) -> str | None:
    """Spanish rejection reason for an upload, or ``None`` when it is acceptable.

    Order matches the spec contract: extension first (the browser-supplied
    filename), then the declared MIME type. The widget layer owns the
    "nothing selected" case; this helper only judges the file itself.
    """
    if not name.lower().endswith(".pdf"):
        return OWNER_UPLOAD_BAD_EXT
    if not mime or "pdf" not in mime.lower():
        return OWNER_UPLOAD_BAD_MIME
    return None


def _stage_upload(data: bytes, directory: Path | None = None) -> Path:
    """Persist an upload under a fresh UUID name inside the staging directory.

    The browser-supplied filename never reaches ingestion: the file is
    renamed to ``{uuid4().hex}.pdf`` before anything reads it. Staging lives
    in ``.uploads/`` (gitignored) so operator uploads can never be committed
    by accident — the previous default of the repository root leaked both the
    bytes and the credentials file sitting next to it.
    """
    target_dir = Path(".uploads").resolve() if directory is None else directory
    target_dir.mkdir(parents=True, exist_ok=True)
    staged = target_dir / f"{uuid.uuid4().hex}.pdf"
    staged.write_bytes(data)
    return staged


def _owner_upload() -> None:
    st.subheader(OWNER_UPLOAD_LABEL)
    st.caption(OWNER_UPLOAD_HELP)
    uploaded = st.file_uploader(
        OWNER_UPLOAD_LABEL,
        type=["pdf"],
        help=OWNER_UPLOAD_HELP,
        key="owner_upload",
    )

    if st.button(OWNER_UPLOAD_LABEL, key="owner_upload_btn"):
        if uploaded is None:
            st.error(OWNER_UPLOAD_EMPTY)
            return
        rejection = _upload_rejection(uploaded.name, uploaded.type)
        if rejection is not None:
            st.error(rejection)
            return
        _do_upload(uploaded)


def _do_upload(uploaded) -> None:
    from plant_rag import config
    from plant_rag.ingest.reindex import manual_id_from_filename, run_reindex
    from plant_rag.retrieve.embedder import get_embedder
    from qdrant_client import QdrantClient

    path = _stage_upload(uploaded.getvalue())

    client = QdrantClient(url=config.QDRANT_URL, timeout=60)
    encode = get_embedder().encode
    # The SAME identity namespace as the CLI: the manual's own filename.
    # A single shared id for every web upload used to collide — goldens
    # never matched and calibration records from different manuals
    # overwrote each other (audit A1).
    manual_id = manual_id_from_filename(uploaded.name)

    try:
        with st.status(OWNER_UPLOAD_IN_PROGRESS, expanded=True) as status:
            status.write(OWNER_UPLOAD_IN_PROGRESS)
            result = run_reindex(
                str(path),
                client=client,
                encode=encode,
                manual_id=manual_id,
                batch_pages=config.BATCH_PAGES,
            )
            status.update(
                label=f"{OWNER_UPLOAD_DONE} (revisión {result.revision})",
                state="complete",
            )
            st.success(OWNER_UPLOAD_DONE)
    except Exception as exc:
        st.error(f"{OWNER_UPLOAD_FAILED}: {exc}")


def _owner_reindex() -> None:
    st.subheader(OWNER_REINDEX_LABEL)
    st.caption(OWNER_REINDEX_HELP)

    if st.button(OWNER_REINDEX_LABEL, key="owner_reindex_btn"):
        _do_reindex()


def _do_reindex() -> None:
    # Reindex without a new PDF is a CLI operation (``scripts/reindex.py``:
    # rebuild the current manual after config tweaks / alias repairs). The web
    # button cannot perform it and only points the operator at the right tool,
    # so it opens no connection and touches no state.
    with st.status(OWNER_REINDEX_IN_PROGRESS, expanded=True) as status:
        status.write(OWNER_REINDEX_IN_PROGRESS)
        status.write("Reindexación manual iniciada…")
        status.update(label=OWNER_REINDEX_FAILED, state="error")
        st.warning(
            "La reindexación desde la web requiere un PDF nuevo. "
            "Use scripts/reindex.py para reconstruir el índice actual."
        )


def _owner_codes(user: auth.User) -> None:
    st.subheader(OWNER_CODES_LABEL)
    st.caption(OWNER_CODES_HELP)

    codes = sorted(_resolve_auth_store().list_codes())
    if not codes:
        st.caption("Aún no hay operadores registrados.")
        return

    st.dataframe(
        data={"Código": codes, "Es propietario": [_resolve_auth_store().has_user(c) and _resolve_auth_store().owner(c) is not None for c in codes]},
        use_container_width=True,
        hide_index=True,
    )

    with st.expander(OWNER_CODES_LABEL):
        _owner_code_add()
        st.divider()
        _owner_code_remove(user)


def _owner_code_add() -> None:
    st.write(OWNER_CODES_LABEL)

    new_code = st.text_input(OWNER_CODE_NEW_LABEL, key="owner_code_new_code")
    new_password = st.text_input(
        OWNER_CODE_NEW_PASSWORD_LABEL,
        type="password",
        key="owner_code_new_password",
    )

    if st.button(OWNER_CODE_ADD, key="owner_code_add_btn"):
        store = _resolve_auth_store()
        if not new_code.strip():
            st.error(OWNER_CODE_NEW_LABEL)
        elif not new_password.strip():
            st.error(OWNER_CODE_NEW_PASSWORD_LABEL)
        elif store.has_user(new_code):
            st.error(OWNER_CODE_ADD_DUPLICATE)
        else:
            store.add_user(new_code, new_password, owner=False)
            st.success(OWNER_CODE_ADD_DONE)
            st.rerun()


def _owner_code_remove(user: auth.User) -> None:
    store = _resolve_auth_store()
    codes = store.list_codes()

    if not codes:
        return

    remove_code = st.selectbox(
        OWNER_CODE_REMOVE,
        options=codes,
        key="owner_code_remove",
    )

    if st.button(OWNER_CODE_REMOVE, key="owner_code_remove_btn"):
        if remove_code == user.code and store.owner(remove_code) is not None:
            remaining_owners = [c for c in codes if c != remove_code and store.owner(c) is not None]
            if not remaining_owners:
                st.error(OWNER_CODE_OWNER_CANNOT_REMOVE)
                return
        try:
            store.remove_user(remove_code)
            st.success(OWNER_CODE_REMOVE_DONE)
            if remove_code == user.code:
                _set_current_user(None)
                st.rerun()
            else:
                st.rerun()
        except Exception as exc:
            st.error(f"{OWNER_CODE_REMOVE_MISSING}: {exc}")


def _page_question() -> None:
    st.header("Preguntar")

    if "last_answer" not in st.session_state:
        st.session_state["last_answer"] = None
    if "queue_position" not in st.session_state:
        st.session_state["queue_position"] = None
    if "metrics_total" not in st.session_state:
        st.session_state["metrics_total"] = 0
    if "metrics_success" not in st.session_state:
        st.session_state["metrics_success"] = 0
    if "metrics_refusal" not in st.session_state:
        st.session_state["metrics_refusal"] = 0
    if "metrics_timeout" not in st.session_state:
        st.session_state["metrics_timeout"] = 0

    with st.expander("Ver métricas de la sesión", expanded=False):
        m_total = st.session_state["metrics_total"]
        m_success = st.session_state["metrics_success"]
        m_refusal = st.session_state["metrics_refusal"]
        m_timeout = st.session_state["metrics_timeout"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Consultas", m_total)
        c2.metric("Con respuesta", m_success)
        c3.metric("Rechazadas", m_refusal)
        c4.metric("Tiempo agotado", m_timeout)
        if m_total:
            st.caption(
                f"Tasa de respuesta útil: {m_success / m_total * 100:.1f}%"
            )
        cumulative = _cumulative_stats()
        if cumulative is not None:
            st.caption(
                f"Acumulado en este equipo: {cumulative['total']} consultas · "
                f"{cumulative['success_rate'] * 100:.1f}% con respuesta · "
                f"p95 {cumulative['p95_latency_s']} s"
            )

    question = st.text_area(
        "Pregunta del operador",
        placeholder=QUESTION_PLACEHOLDER,
        key="question_input",
    )

    col1, col2 = st.columns([4, 1])
    with col1:
        ask = st.button(QUESTION_SEND, type="primary", key="ask_btn")
    with col2:
        refresh_position = st.button(QUEUE_POSITION_REFRESH, key="refresh_position_btn")

    store = _resolve_auth_store()
    scheduler = _scheduler()
    position_text = queue_position_es(1)

    if refresh_position:
        st.session_state["queue_position"] = position_text

    if st.session_state.get("queue_position"):
        st.info(st.session_state["queue_position"])

    if _is_busy(st.session_state):
        st.warning(QUESTION_BUSY)
        return

    if ask:
        if not question.strip():
            st.error(QUESTION_EMPTY)
            return
        _mark_busy(st.session_state)
        st.session_state["last_answer"] = None
        # A new question starts a new rating cycle: the previous answer's
        # vote state (and the question it belongs to) must not leak onto
        # the next rateable answer (operator-feedback spec: "asking a new
        # question resets that state").
        st.session_state["last_answer_voted"] = False
        st.session_state.pop("last_answer_question", None)

        try:
            answer = _run_question(scheduler, question, store)
        except LLMError:
            st.error(f"{ANSWER_INFRA_ERROR_LABEL}: {ANSWER_INFRA_ERROR_PROMPT}")
            answer = None
        except Exception:
            st.error(f"{ANSWER_INFRA_ERROR_LABEL}: {ANSWER_INFRA_ERROR_PROMPT}")
            answer = None
        finally:
            _clear_busy(st.session_state)

        if answer is not None:
            st.session_state["last_answer"] = answer
            st.session_state["last_answer_question"] = question

    last_answer = st.session_state.get("last_answer")
    _render_answer(last_answer)
    _feedback_widget(last_answer)


# "In progress" is a TIMESTAMP, not a boolean. A boolean gets stuck when a
# rerun dies mid-question (browser refresh, dropped websocket, Streamlit
# killing the script thread): the finally never runs, the flag stays true,
# and every later rerun shows "already in progress" forever. A timestamp
# self-heals: a flag older than the deadline plus margin is stale by
# definition, because no question may legally outlive that budget.
BUSY_KEY = "answer_busy_at"
_BUSY_STALE_MARGIN_S = 30.0


def _is_busy(state, now: float | None = None) -> bool:
    """True while a question is genuinely running; a stale flag reads False."""
    started = state.get(BUSY_KEY)
    if started is None:
        return False
    if now is None:
        now = time.monotonic()
    if now - started > config.QUERY_DEADLINE_S + _BUSY_STALE_MARGIN_S:
        state.pop(BUSY_KEY, None)  # self-heal: the run that set it is gone
        return False
    return True


def _mark_busy(state) -> None:
    state[BUSY_KEY] = time.monotonic()


def _clear_busy(state) -> None:
    state.pop(BUSY_KEY, None)


# ONE scheduler per server process: module state survives Streamlit reruns,
# so the queue is real across sessions. A scheduler built per call would give
# every question its own private queue (positions always 1, rule-based safety
# preemption and aging never observable).
_SCHEDULER: Scheduler | None = None
_ANSWERS: dict[str, Answer] = {}


def _scheduler() -> Scheduler:
    global _SCHEDULER
    if _SCHEDULER is None:
        _SCHEDULER = Scheduler(_generate_and_record)
    return _SCHEDULER


def _generate_and_record(question: str, remaining_s: float) -> Answer:
    """The scheduler's generator: the shared chain, with answer routing.

    ``run_once`` serves the highest-priority entry whichever session
    submitted it, so the answer is recorded by question text and every session
    picks up its own. A key collision is benign: identical questions produce
    identical answers (the corpus is the same for everyone).
    """
    from plant_rag.answer.pipeline import answer_question

    answer = answer_question(
        question, remaining_s, log=_query_log(), cache=_answer_cache()
    )
    # Bounded: a console runs for days. Evict the OLDEST entries (dicts are
    # insertion-ordered) instead of clearing the map — a clear could drop
    # another session's answer that is still in flight.
    _ANSWERS[question] = answer
    while len(_ANSWERS) > 1000:
        _ANSWERS.pop(next(iter(_ANSWERS)))
    return answer


_QUERY_LOG: "QueryLog | None" = None
_QUERY_LOG_RESOLVED = False


def _query_log() -> "QueryLog | None":
    """The configured operator-query log, resolved once per process."""
    global _QUERY_LOG, _QUERY_LOG_RESOLVED
    if not _QUERY_LOG_RESOLVED:
        from plant_rag.monitoring import QueryLog

        _QUERY_LOG_RESOLVED = True
        _QUERY_LOG = QueryLog.from_config()
    return _QUERY_LOG


_ANSWER_CACHE: object = None
_ANSWER_CACHE_RESOLVED = False


def _answer_cache():
    """The configured revision-scoped answer cache, resolved once per process.

    One shared instance per server process (like ``_query_log``): the LRU
    index stays warm across Streamlit reruns and concurrent sessions, so a
    repeat question is served from memory while the JSONL file stays the
    durable copy. ``None`` when the cache is disabled by configuration.
    """
    global _ANSWER_CACHE, _ANSWER_CACHE_RESOLVED
    if not _ANSWER_CACHE_RESOLVED:
        from plant_rag.answer.cache import AnswerCache

        _ANSWER_CACHE_RESOLVED = True
        _ANSWER_CACHE = AnswerCache.from_config()
    return _ANSWER_CACHE


def _cumulative_stats() -> dict | None:
    """Aggregate view of every operator question logged on this machine."""
    log = _query_log()
    if log is None:
        return None
    from plant_rag.monitoring import stats

    return stats(log.records())


def _record_metrics(answer: Answer) -> None:
    """Count one served question for the session metrics panel."""
    st.session_state["metrics_total"] = st.session_state.get("metrics_total", 0) + 1
    kind = getattr(answer, "kind", None)
    if kind == "answer":
        st.session_state["metrics_success"] = (
            st.session_state.get("metrics_success", 0) + 1
        )
    elif kind == "refusal":
        st.session_state["metrics_refusal"] = (
            st.session_state.get("metrics_refusal", 0) + 1
        )
    elif kind == "timeout":
        st.session_state["metrics_timeout"] = (
            st.session_state.get("metrics_timeout", 0) + 1
        )


def _run_question(
    scheduler: Scheduler, question: str, store: auth.AuthStore
) -> Answer | None:
    position = scheduler.submit(question)
    st.session_state["queue_position"] = queue_position_es(position)
    _ANSWERS.pop(question, None)  # only a fresh answer may satisfy this ask

    # Drain whatever is pending. The entry served may belong to another
    # session — its answer was recorded by the same wrapper, and that session
    # picks it up from _ANSWERS by its own question text.
    while scheduler.run_once() is not None:
        pass

    answer = _ANSWERS.get(question)
    if answer is None:
        # Rare race: our submit raced a concurrent drain. Poll briefly so a
        # question is never dropped while its answer exists in flight.
        for _ in range(20):
            answer = _ANSWERS.get(question)
            if answer is not None:
                break
            time.sleep(0.25)

    if answer is not None:
        _record_metrics(answer)
    return answer


def _cited_excerpts(citations: tuple) -> dict[tuple[str, int], str]:
    """Fetch the manual passage behind each citation from the live corpus.

    One scroll over the current revision, filtered to the cited pages: the
    operator verifies the answer without opening the PDF. A failure (Qdrant
    down, alias missing) returns an empty map — the excerpt is a trust aid,
    never a requirement for showing the answer.
    """
    if not citations:
        return {}
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        from plant_rag.retrieve.store import TEXT_ALIAS, current_target

        client = QdrantClient(url=config.QDRANT_URL, timeout=10)
        target = current_target(client, TEXT_ALIAS)
        if target is None:
            return {}
        points, _ = client.scroll(
            collection_name=target,
            scroll_filter=Filter(
                should=[
                    Filter(
                        must=[
                            FieldCondition(
                                key="manual_id",
                                match=MatchValue(value=manual_id),
                            ),
                            FieldCondition(key="page", match=MatchValue(value=page)),
                        ]
                    )
                    for manual_id, page in {
                        (c.manual_id, c.page) for c in citations
                    }
                ]
            ),
            limit=50,
            with_payload=True,
            with_vectors=False,
        )
        excerpts: dict[tuple[str, int], str] = {}
        for point in points:
            payload = point.payload or {}
            key = (str(payload.get("manual_id", "")), int(payload.get("page", 0)))
            text = str(payload.get("parent_text", "")).strip()
            if text and key not in excerpts:
                excerpts[key] = text
        return excerpts
    except Exception:
        return {}


def _render_answer(answer: Answer | None) -> None:
    if answer is None:
        return

    if answer.kind == "timeout":
        st.header(ANSWER_TIMEOUT_LABEL)
        st.info(ANSWER_TIMEOUT_PROMPT)
        return

    if answer.kind == "refusal":
        # The contract text, verbatim: the pipeline builds refusal Answers
        # with exactly REFUSAL_ES by construction (never a paraphrase), so the
        # UI shows answer.text_es. A second wording here would fork the
        # contract: CLI, eval and tests assert the exact string, the web must
        # show the same one the operator already receives elsewhere.
        st.header(ANSWER_LABEL)
        st.warning(answer.text_es)
        return

    st.header(ANSWER_LABEL)
    st.write(answer.text_es)

    if answer.citations:
        st.subheader(ANSWER_CITATIONS_LABEL)
        excerpts = _cited_excerpts(answer.citations)
        seen: set[tuple[str, int]] = set()
        for citation in answer.citations:
            key = (citation.manual_id, citation.page)
            if key in seen:
                continue  # the answer often cites the same page per step
            seen.add(key)
            st.caption(f"[Fuente: {citation.manual_id} Página {citation.page}]")
            excerpt = excerpts.get(key)
            if excerpt:
                with st.expander(
                    f"{ANSWER_EXCERPT_LABEL} {citation.page}", expanded=False
                ):
                    st.text(excerpt[:2000])


def _feedback_widget(answer: Answer | None) -> None:
    """Thumbs rating for the last rateable answer (operator-feedback spec).

    Called from ``_page_question``, NEVER from ``_render_answer``: the
    render stays a pure display of what the pipeline produced, while the
    rating control is page state (session flag, vote recording). Only
    grounded answers and refusals are rateable — a timeout notice has
    nothing to rate (spec assumption A1).
    """
    if answer is None or answer.kind not in ("answer", "refusal"):
        return
    if st.session_state.get("last_answer_voted"):
        st.caption(FEEDBACK_DONE)
        return
    st.caption(FEEDBACK_LABEL)
    # on_click, not "if pressed": Streamlit runs the callback BEFORE the
    # rerun renders, so the confirmation replaces the control in the very
    # run where the operator clicked (a vote is recorded, then disabled).
    left, right = st.columns(2)
    with left:
        st.button(
            FEEDBACK_THUMBS_UP,
            key="feedback_thumbs_up",
            on_click=_cast_feedback,
            args=(answer, "up"),
        )
    with right:
        st.button(
            FEEDBACK_THUMBS_DOWN,
            key="feedback_thumbs_down",
            on_click=_cast_feedback,
            args=(answer, "down"),
        )


def _cast_feedback(answer: Answer, vote: str) -> None:
    """Record one vote best-effort, then disable the control for this answer.

    The write path is best-effort by contract (``record_feedback`` swallows
    its own IO failures): a full disk must never block the operator. The
    session flag flips regardless, so one shown answer accepts exactly one
    vote even when the write failed.
    """
    log = _query_log()
    if log is not None:
        log.record_feedback(
            st.session_state.get("last_answer_question", ""),
            answer.kind,
            vote,
            model=config.OLLAMA_MODEL,
        )
    st.session_state["last_answer_voted"] = True


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def prewarm_models() -> dict[str, bool]:
    """Load the three heavy models, once, before the first operator question.

    A cold start costs ~35-40s (BGE-M3 ~12s + reranker ~8s + qwen2.5 load
    ~15-20s) and it lands on whoever asks first, inside the same 45s budget the
    answer uses (ADR-005). Best-effort by design: a failed warm-up is reported
    for the server console and the app serves anyway — the first question then
    pays that load exactly as it did before.

    Disabled by ``PLANT_RAG_PREWARM_MODELS=0`` (the hermetic suite sets it, so
    driving the real app through AppTest never loads a checkpoint or calls
    Ollama). Returns the per-model readiness map, empty when disabled.
    """
    if not config.PREWARM_MODELS:
        return {}

    from plant_rag.answer import llm as llm_mod
    from plant_rag.retrieve import rerank as rerank_mod
    from plant_rag.retrieve.embedder import get_embedder

    ready: dict[str, bool] = {}
    try:
        get_embedder().encode(["calentamiento"])
        ready["embedder"] = True
    except Exception:  # noqa: BLE001 - warm-up must never block the app
        ready["embedder"] = False
    ready["reranker"] = rerank_mod.warmup()
    ready["llm"] = llm_mod.warmup()
    return ready


@st.cache_resource(show_spinner=PREWARM_LABEL)
def _prewarm_models() -> dict[str, bool]:
    """Once per process: Streamlit reruns this file on every interaction."""
    return prewarm_models()


def main() -> None:
    """Login gate first, then the app page for the session's user."""
    _prewarm_models()
    if _current_user() is None:
        _page_login()
    else:
        _page_app()


# Streamlit runs this file as ``__main__`` (streamlit run, AppTest); a plain
# ``import web.app`` must stay side-effect free.
if __name__ == "__main__":
    main()
