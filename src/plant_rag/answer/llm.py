"""One bounded Ollama call behind the refusal and citation gates (ADR-004).

Conventions established here:

* The empty-result gate comes FIRST: with no retrieved evidence the exact
  ``REFUSAL_ES`` is returned without any network traffic — an LLM call on an
  empty corpus can only invent (answering spec: "Exact not-found refusal").
* Generation options are the ADR-004 contract: ``num_ctx=8192``,
  ``temperature=0.2``, ``num_predict<=600``. They are sent on every call so
  no server-side default can widen the context or the answer length.
* The deadline gates generation: the transport only ever gets the budget the
  queue has left (``remaining_s``), and a response that arrives after the
  budget is discarded in favor of ``DeadlineExceeded`` — the scheduler turns
  that into the Spanish timeout, never a late or partial answer (ADR-005).
* Everything the model returns passes the citation validator before it can
  become an ``Answer``. A citation violation triggers ONE bounded corrective
  retry (budget permitting); a second violation collapses to the exact
  refusal. A safety-topic question additionally carries the Spanish warning —
  the model is never trusted to emit either itself.
* :func:`post_chat` is public: the CLI, eval, and web layers used to carry
  three verbatim copies of this urllib transport, so one entrypoint picked up
  an error-handling fix and the others kept the bug. They import it instead.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from plant_rag import config
from plant_rag.answer.citations import CitationViolation, _MARKER_RE, validate_citations
from plant_rag.answer.prompt import (
    SAFETY_WARNING_ES,
    build_messages,
    is_safety_query,
)
from plant_rag.models import REFUSAL_ES, Answer, Hit

# ADR-004: 8k context fits the top-k parents, low temperature keeps the
# Spanish answer extractive, <=600 tokens keeps answers inside one screen.
GENERATION_OPTIONS: dict[str, object] = {
    "num_ctx": 8192,
    "temperature": 0.2,
    "num_predict": 600,
}

# ONE bounded citation retry. A 7B model fumbles the marker often enough
# (measured: 1 in 3 on a live manual) that a second, corrected call converts
# most of those into the grounded answer it had already written. The retry
# only runs for citation violations (never for LLMError) and only with budget
# to spare - no loop-until-clean, and the contract's exact refusal remains
# the terminal state when the second attempt also fails.
CITATION_RETRY_MIN_BUDGET_S = 10.0
CITATION_RETRY_PROMPT_ES = (
    "Tu cita no se corresponde con los extractos. Vuelve a responder usando "
    "SOLO las etiquetas [Fuente: …] que aparecen en los extractos anteriores, "
    "nunca otras páginas."
)

# A transport call for Ollama's /api/chat: (url, payload, timeout seconds)
# -> decoded JSON. Injected in tests; the default is :func:`_post_chat`.
Transport = Callable[[str, dict, float], dict]


class DeadlineExceeded(Exception):
    """The 45s end-to-end budget ran out before generation finished (ADR-005)."""


class LLMError(Exception):
    """Ollama was unreachable or answered something unreadable — never invented."""


def post_chat(url: str, payload: dict, timeout_s: float) -> dict:
    """POST one chat request to Ollama with a hard read timeout."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except TimeoutError:
        raise  # the caller maps an exhausted budget onto DeadlineExceeded
    except urllib.error.HTTPError as exc:
        raise LLMError(f"Ollama answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError("Ollama read timed out") from exc
        raise LLMError(f"Ollama unreachable: {exc.reason}") from exc
    except OSError as exc:
        raise LLMError(f"Ollama request failed: {exc}") from exc
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMError("Ollama returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise LLMError("Ollama returned a non-object response")
    return decoded


def _extract_content(response: dict) -> str:
    """Read the assistant message out of an Ollama chat response, or fail."""
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise LLMError("Ollama response carries no assistant content")
    return content


def apply_safety_warning(answer: Answer, question: str) -> Answer:
    """Prepend the Spanish safety warning to a grounded safety-topic answer.

    Refusals and timeouts are left untouched: those contract strings are
    exact and must reach the operator unmodified.
    """
    if answer.kind != "answer" or not is_safety_query(question):
        return answer
    return Answer(
        kind="answer",
        text_es=f"{SAFETY_WARNING_ES}\n\n{answer.text_es}",
        citations=answer.citations,
    )


def _is_refusal_text(text: str) -> bool:
    """True when the model's text IS the exact refusal, citations aside.

    A 7B model sometimes declines correctly AND copies the citation format
    ("No aparece en la documentación [Fuente: … Página 155]"). The marker
    then validates (the page WAS retrieved) and the operator sees a refusal
    presented as an answer. The contract is the KIND, not the model's
    punctuation: refusal text means refusal, whatever trails it.
    """
    without_brackets = re.sub(r"\[[^\]]*\]", " ", text)
    normalized = re.sub(r"\s+", " ", without_brackets).strip().casefold()
    return normalized == REFUSAL_ES.casefold()


def generate(
    question: str,
    hits: tuple[Hit, ...],
    *,
    remaining_s: float | None = None,
    transport: Transport = post_chat,
) -> Answer:
    """Produce the operator's answer under the full ADR-004 contract.

    Args:
        question: Raw operator query (Spanish).
        hits: Retrieved passages; empty means "no evidence" and short-circuits.
        remaining_s: Seconds of the 45s budget left (queue hands this over).
            ``None`` falls back to ``config.QUERY_DEADLINE_S`` for direct use.
        transport: Injected Ollama transport; the default hits the network.

    Returns:
        A grounded ``Answer`` (safety warning applied), or the exact refusal
        when there is no evidence or the model violated the citation contract.

    Raises:
        ValueError: if ``question`` is blank.
        DeadlineExceeded: if the budget is already gone or generation
            outlived it — the caller emits the Spanish timeout instead.
        LLMError: if Ollama is unreachable or its response is unusable.
    """
    if not question.strip():
        raise ValueError("question must not be empty")

    # Evidence gate: no passages -> exact refusal, ZERO network traffic.
    if not hits:
        return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())

    timeout_s = (
        float(remaining_s) if remaining_s is not None else float(config.QUERY_DEADLINE_S)
    )
    if timeout_s <= 0:
        raise DeadlineExceeded("the end-to-end budget was already exhausted")

    messages = build_messages(question, hits)
    url = config.OLLAMA_URL.rstrip("/") + "/api/chat"
    started = time.monotonic()

    text = _generate_once(
        transport,
        url,
        {
            "model": config.OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "options": dict(GENERATION_OPTIONS),
        },
        timeout_s,
        started,
    )

    if _is_refusal_text(text):
        # The model declined: refusal KIND, terminal, citation or not.
        return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())

    try:
        citations = validate_citations(text, hits)
    except CitationViolation:
        if not _MARKER_RE.search(text):
            # The model declined on its own (no citation attempted at all):
            # that IS the refusal, not a citation fumble. Retrying would
            # pressure it into citing unrelated evidence (measured live: an
            # out-of-corpus question about cooking pasta got "answered" with
            # the horn page after such a nudge).
            return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())
        # ONE bounded retry, only when the model TRIED to cite and the marker
        # failed (wrong page, unparseable, whitespace) and the budget still
        # covers a generation: a 7B model fumbles the marker often enough
        # (measured 1 in 3 on a live manual) that retrying converts most of
        # those into the grounded answer it already wrote.
        # No loop-until-clean: the second failure is the exact refusal.
        retry_timeout_s = timeout_s - (time.monotonic() - started)
        if retry_timeout_s >= CITATION_RETRY_MIN_BUDGET_S:
            retry_messages = messages + [
                {
                    "role": "user",
                    "content": CITATION_RETRY_PROMPT_ES,
                }
            ]
            retry_started = time.monotonic()
            try:
                text = _generate_once(
                    transport,
                    url,
                    {
                        "model": config.OLLAMA_MODEL,
                        "messages": retry_messages,
                        "stream": False,
                        "options": dict(GENERATION_OPTIONS),
                    },
                    retry_timeout_s,
                    retry_started,
                )
            except DeadlineExceeded:
                return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())
            if _is_refusal_text(text):
                # Second decline: the retry answer is also a refusal.
                return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())
            try:
                citations = validate_citations(text, hits)
            except CitationViolation:
                return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())
        else:
            return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())

    answer = Answer(kind="answer", text_es=text.strip(), citations=citations)
    return apply_safety_warning(answer, question)


def _generate_once(
    transport: Transport,
    url: str,
    payload: dict,
    timeout_s: float,
    started: float,
) -> str:
    """One Ollama call under the shared deadline, returning the raw text.

    Raises:
        DeadlineExceeded: the budget ran out (or a late transport ignored it).
        LLMError: Ollama was unreachable or its response unusable - the caller
            does NOT retry those (infrastructure, not a citation fumble).
    """
    try:
        response = transport(url, payload, timeout_s)
    except DeadlineExceeded:
        raise
    except TimeoutError as exc:
        raise DeadlineExceeded("generation consumed the whole budget") from exc
    except LLMError:
        raise
    except OSError as exc:
        raise LLMError(f"Ollama transport failed: {exc}") from exc

    if time.monotonic() - started > timeout_s:
        # The transport ignored its own timeout: a late answer is a timeout,
        # never a delivered reply past the deadline (ADR-005).
        raise DeadlineExceeded("generation finished after the deadline")

    return _extract_content(response)
