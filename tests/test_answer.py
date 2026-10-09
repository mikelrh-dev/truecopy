"""Answering tests (Phase 4, Work Unit 4).

Hermetic by contract: no Ollama, no Qdrant, no model. The LLM is an injected
transport double, and the relevance gate is exercised through the in-memory
Qdrant double shared with ``test_retrieve``. The real ``qwen2.5:7b-instruct``
path is proven by the runtime harness reported separately from this suite.
"""

from __future__ import annotations

import time

import pytest

from plant_rag import config
from plant_rag.answer.citations import CitationViolation, validate_citations
from plant_rag.answer.llm import (
    CITATION_RETRY_MIN_BUDGET_S,
    GENERATION_OPTIONS,
    DeadlineExceeded,
    LLMError,
    apply_safety_warning,
    generate,
)
from plant_rag.answer.prompt import (
    SAFETY_TERMS,
    SAFETY_WARNING_ES,
    SYSTEM_PROMPT_ES,
    build_messages,
    is_safety_query,
)
from plant_rag.models import REFUSAL_ES, Answer, Citation, Hit
from plant_rag.retrieve import store
from test_retrieve import FakeQdrant, query_vector, text_payload


# --- doubles ---------------------------------------------------------------


class FakeTransport:
    """Ollama /api/chat stand-in: records every call, replays one outcome."""

    def __init__(self, outcome=None) -> None:
        # A string becomes a normal assistant reply; an exception is raised.
        self.outcome = outcome
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout_s: float) -> dict:
        self.calls.append({"url": url, "payload": payload, "timeout_s": timeout_s})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        content = "ok" if self.outcome is None else self.outcome
        return {"message": {"role": "assistant", "content": content}}


def grounded_hits() -> tuple[Hit, ...]:
    """Two retrieved passages: pages 12 and 48 of manual 828D."""
    return (
        Hit(
            manual_id="828D",
            page=12,
            revision="r1",
            child="The coolant pressure switch opens at 1.2 bar.",
            parent="4.3 Coolant pressure monitoring — full English page text.",
        ),
        Hit(
            manual_id="828D",
            page=48,
            revision="r1",
            child="Reset the traverse axis before homing.",
            parent="7.1 Traverse axis reset — full English page text.",
        ),
    )


VALID_ANSWER_ES = (
    "La presión del refrigerante se revisa con la máquina fría. "
    "[Fuente: 828D Página 12]"
)


# --- 4.1 prompt: Spanish system prompt, closed safety terms, warning --------


def test_system_prompt_is_spanish_and_carries_the_exact_refusal_rule() -> None:
    assert "siempre en español" in SYSTEM_PROMPT_ES
    # The prompt and the contract string cannot drift: it is interpolated.
    assert REFUSAL_ES in SYSTEM_PROMPT_ES
    assert "[Fuente: <manual_id> Página <número>]" in SYSTEM_PROMPT_ES


def test_build_messages_labels_excerpts_with_the_citation_format() -> None:
    messages = build_messages("¿cómo reviso la presión?", grounded_hits())

    assert [message["role"] for message in messages] == ["system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT_ES
    user = messages[1]["content"]
    assert "[Fuente: 828D Página 12]" in user
    assert "[Fuente: 828D Página 48]" in user
    assert "Coolant pressure monitoring" in user  # English evidence kept verbatim
    assert user.endswith("Pregunta del operador: ¿cómo reviso la presión?")


def test_build_messages_collapses_duplicated_parent_passages() -> None:
    # top-k children can map to one page-sized parent: label it once.
    first, _ = grounded_hits()
    messages = build_messages("q", (first, first))

    assert messages[1]["content"].count("[Fuente: 828D Página 12]") == 1


@pytest.mark.parametrize(
    "query",
    [
        "¿Hay peligro eléctrico al abrir la puerta?",
        "ALTA TENSIÓN dentro del armario",
        "procedimiento de EVACUACIÓN",
        "revisión de seguridad",
        "¿cuál es el riesgo de choque?",
        "precaución al girar el husillo",
        "Emergencias: paro inmediato",
    ],
)
def test_safety_detection_matches_the_closed_term_set(query: str) -> None:
    assert is_safety_query(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "¿cómo reviso la presión del refrigerante?",
        "peligrosa deformación del husillo",  # substring, not a term
        "¿qué significa la alarma 12345?",
        "",
        "   ",
    ],
)
def test_routine_queries_are_not_safety(query: str) -> None:
    assert is_safety_query(query) is False


def test_safety_set_is_closed_and_normalized() -> None:
    # Rule-based over closed terms (query-queue spec): no classifier, and
    # every entry is accent-stripped so the matcher stays auditable.
    assert isinstance(SAFETY_TERMS, tuple)
    assert all(term == term.casefold() for term in SAFETY_TERMS)
    assert "seguridad" in SAFETY_TERMS


def test_safety_warning_is_an_operator_facing_spanish_string() -> None:
    assert SAFETY_WARNING_ES.startswith("Advertencia de seguridad:")


# --- 4.3 citation validator -------------------------------------------------


def test_validator_accepts_citations_built_from_retrieved_evidence() -> None:
    text = f"Primera afirmación. [Fuente: 828D Página 12] Segunda. [Fuente: 828D Página 48]"

    citations = validate_citations(text, grounded_hits())

    assert citations == (Citation("828D", 12), Citation("828D", 48))


@pytest.mark.parametrize(
    "text",
    [
        "Afirmación sin respaldo. [Fuente: 828D Página 999]",  # invented page
        "Afirmación sin respaldo. [Fuente: FAKE-9 Página 12]",  # invented manual
        "La presión es 1.2 bar.",  # no citation at all
        "[Fuente: 828D sin página]",  # citation-shaped but unparseable
        "[Fuente: 828D Page 12]",  # wrong field label: cannot prove a page
    ],
)
def test_validator_rejects_anything_not_proven_by_retrieval(text: str) -> None:
    with pytest.raises(CitationViolation):
        validate_citations(text, grounded_hits())


def test_validator_accepts_case_and_accent_variants_of_the_marker() -> None:
    # Typography is not the contract: WHAT was cited is.
    citations = validate_citations(
        "Respuesta. [fuente: 828D pagina 48]", grounded_hits()
    )
    assert citations == (Citation("828D", 48),)


def test_validator_accepts_whitespace_drift_inside_the_marker() -> None:
    # Live regression: a grounded, correct answer from the 7B was refused
    # over ONE space ("[ Fuente: … Página 109 ]"). Whitespace around the
    # brackets is typography, not contract.
    citations = validate_citations(
        "Respuesta. [ Fuente: 828D Página 48 ]", grounded_hits()
    )
    assert citations == (Citation("828D", 48),)


def test_system_prompt_forbids_citing_pages_mentioned_inside_excerpts() -> None:
    # A small model reading a cross-reference ("see page 109") cited that
    # page instead of the excerpt's own label - a page retrieval never
    # returned, so the answer collapsed to the refusal.
    assert "NO la cites" in SYSTEM_PROMPT_ES
    assert "etiqueta [Fuente:" in SYSTEM_PROMPT_ES


def test_citation_value_object_validates_at_the_boundary() -> None:
    with pytest.raises(ValueError, match="manual_id"):
        Citation(manual_id="  ", page=1)
    with pytest.raises(ValueError, match="page"):
        Citation(manual_id="828D", page=0)


def test_answer_value_object_enforces_the_never_invent_contract() -> None:
    with pytest.raises(ValueError, match="kind"):
        Answer(kind="summary", text_es="hola", citations=())
    with pytest.raises(ValueError, match="text_es"):
        Answer(kind="refusal", text_es="  ", citations=())
    with pytest.raises(ValueError, match="at least"):
        Answer(kind="answer", text_es="afirmación sin fuente", citations=())
    with pytest.raises(ValueError, match="must not carry"):
        Answer(kind="refusal", text_es=REFUSAL_ES, citations=(Citation("828D", 12),))
    with pytest.raises(ValueError, match="Citation"):
        Answer(kind="answer", text_es="x", citations=("828D", 12))  # type: ignore[arg-type]


# --- 4.2 llm: refusal gate, options, grounded answer, warning ---------------


def test_empty_hits_return_the_exact_refusal_without_an_llm_call() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    answer = generate("¿cuál es la presión?", (), transport=transport)

    assert (answer.kind, answer.text_es, answer.citations) == (
        "refusal",
        REFUSAL_ES,
        (),
    )
    assert transport.calls == []  # ADR-004: refusal without an LLM call


def test_low_score_results_route_to_refusal_without_an_llm_call() -> None:
    # Out-of-corpus measurements from the batch-3 harness (0.365/0.314) sit
    # below MIN_SCORE: retrieval yields () and the same refusal gate fires.
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=1),
        text_payload(page=2),
        scores=[0.365, 0.314],
    )
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    hits = store.search(fake, query_vector())
    answer = generate("pregunta fuera del corpus", hits, transport=transport)

    assert hits == ()
    assert answer.kind == "refusal" and answer.text_es == REFUSAL_ES
    assert transport.calls == []


def test_relevant_results_pass_the_relevance_gate_into_the_llm() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=12),
        text_payload(page=48),
        scores=[0.571, 0.467],
    )
    transport = FakeTransport(
        outcome="La presión se revisa en frío. [Fuente: 828D Página 12]"
    )

    hits = store.search(fake, query_vector())
    answer = generate("¿cómo reviso la presión?", hits, transport=transport)

    assert len(hits) == 2  # both measured in-corpus scores survived the floor
    assert answer.kind == "answer"
    assert answer.citations == (Citation("828D", 12),)


def test_min_score_and_deadline_defaults_match_the_recorded_decision() -> None:
    # Calibration note lives in config.py: 0.571/0.467 in-corpus vs
    # 0.365/0.314 out-of-corpus -> floor 0.40, deadline 45s (A1).
    assert config.MIN_SCORE == 0.40
    assert config.QUERY_DEADLINE_S == 45


def test_config_rejects_invalid_float_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANT_RAG_MIN_SCORE", "fuerte")
    with pytest.raises(ValueError, match="PLANT_RAG_MIN_SCORE"):
        config._env_float("MIN_SCORE", 0.40)


def test_generation_payload_matches_the_adr_004_contract() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    generate("¿cómo reviso la presión?", grounded_hits(), transport=transport)

    call = transport.calls[0]
    assert call["url"] == config.OLLAMA_URL.rstrip("/") + "/api/chat"
    payload = call["payload"]
    assert payload["model"] == config.OLLAMA_MODEL
    assert payload["stream"] is False
    assert payload["options"] == GENERATION_OPTIONS
    assert GENERATION_OPTIONS["num_ctx"] == 8192
    assert GENERATION_OPTIONS["temperature"] == 0.2
    assert GENERATION_OPTIONS["num_predict"] <= 600
    assert call["timeout_s"] == pytest.approx(config.QUERY_DEADLINE_S)


def test_grounded_answer_keeps_spanish_text_and_proven_citations() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    answer = generate("¿cómo reviso la presión?", grounded_hits(), transport=transport)

    assert answer.kind == "answer"
    assert answer.text_es == VALID_ANSWER_ES
    assert answer.citations == (Citation("828D", 12),)


@pytest.mark.parametrize(
    "model_output",
    [
        "La presión es 1.2 bar. [Fuente: 828D Página 999]",  # page not retrieved
        "La presión es 1.2 bar.",  # no citation: an uncited claim
    ],
)
def test_citation_violation_collapses_to_the_exact_refusal(model_output: str) -> None:
    transport = FakeTransport(outcome=model_output)

    answer = generate("¿cómo reviso la presión?", grounded_hits(), transport=transport)

    assert (answer.kind, answer.text_es, answer.citations) == (
        "refusal",
        REFUSAL_ES,
        (),
    )


class ScriptedTransport:
    """Replays one outcome per call, so a retry can differ from its first try."""

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout_s: float) -> dict:
        self.calls.append({"url": url, "payload": payload, "timeout_s": timeout_s})
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return {"message": {"role": "assistant", "content": outcome}}


def test_a_citation_fumble_is_retried_once_and_recovers() -> None:
    # The 7B wrote the grounded answer but fumbled the marker (measured live:
    # 1 in 3). The bounded retry converts it instead of refusing.
    transport = ScriptedTransport(
        "El offset es fijo. [Fuente: 828D Página 999]",  # page not retrieved
        VALID_ANSWER_ES,
    )

    answer = generate("¿cómo ajusto el offset?", grounded_hits(), transport=transport)

    assert answer.kind == "answer"
    assert answer.citations == (Citation("828D", 12),)
    assert len(transport.calls) == 2
    # The corrective call appends the citation-nudge message, keeping the
    # original [system, user] shape intact.
    retry_messages = transport.calls[1]["payload"]["messages"]
    assert [m["role"] for m in retry_messages] == ["system", "user", "user"]
    assert "SOLO las etiquetas" in retry_messages[-1]["content"]


def test_a_citation_fumble_retries_at_most_once() -> None:
    # No loop-until-clean: the second failure is terminal.
    transport = ScriptedTransport(
        "A. [Fuente: 828D Página 999]",
        "B. [Fuente: 828D Página 999]",
    )

    answer = generate("¿cómo ajusto el offset?", grounded_hits(), transport=transport)

    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert len(transport.calls) == 2


def test_no_retry_when_the_budget_cannot_cover_one() -> None:
    transport = ScriptedTransport("A. [Fuente: 828D Página 999]", VALID_ANSWER_ES)

    answer = generate(
        "¿cómo ajusto el offset?",
        grounded_hits(),
        remaining_s=CITATION_RETRY_MIN_BUDGET_S - 1.0,
        transport=transport,
    )

    assert answer.kind == "refusal"
    assert len(transport.calls) == 1  # never burned budget the answer needed


def test_llm_errors_are_never_retried() -> None:
    # Infrastructure failures are not citation fumbles: one call, propagate.
    transport = ScriptedTransport(LLMError("ollama down"))

    with pytest.raises(LLMError):
        generate("¿cómo ajusto el offset?", grounded_hits(), transport=transport)

    assert len(transport.calls) == 1


def test_a_model_that_declines_on_its_own_is_not_retried() -> None:
    # No citation marker means the model REFUSED - retrying would pressure it
    # into citing unrelated evidence (measured: an out-of-corpus pasta
    # question got "answered" with the horn page after such a nudge).
    transport = ScriptedTransport(REFUSAL_ES, VALID_ANSWER_ES)

    answer = generate("¿cómo se cocina la pasta?", grounded_hits(), transport=transport)

    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert len(transport.calls) == 1  # the second outcome was never requested


def test_a_decline_that_carries_a_valid_citation_is_still_a_refusal() -> None:
    # Live regression ("modo perro"): the model declined AND copied the
    # citation format. The marker validated, so the operator saw a refusal
    # presented as an answer. Refusal text means refusal, whatever trails it.
    transport = ScriptedTransport(
        f"{REFUSAL_ES} [Fuente: 828D Página 15]", VALID_ANSWER_ES
    )

    answer = generate("¿qué es el modo perro?", grounded_hits(), transport=transport)

    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == ()
    assert len(transport.calls) == 1  # no retry: it is not a citation fumble


def test_safety_question_answer_carries_the_spanish_warning() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    answer = generate(
        "¿cuál es el riesgo eléctrico al abrir la puerta?",
        grounded_hits(),
        transport=transport,
    )

    assert answer.kind == "answer"
    assert answer.text_es.startswith(SAFETY_WARNING_ES)
    assert VALID_ANSWER_ES in answer.text_es
    assert answer.citations == (Citation("828D", 12),)


def test_routine_answer_carries_no_warning() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    answer = generate("¿cómo reviso la presión?", grounded_hits(), transport=transport)

    assert SAFETY_WARNING_ES not in answer.text_es


def test_refusal_stays_exact_even_for_a_safety_question() -> None:
    answer = generate("¿cuál es el riesgo eléctrico?", (), transport=FakeTransport())

    assert answer.text_es == REFUSAL_ES  # warning never pollutes the contract
    assert answer.kind == "refusal"


def test_apply_safety_warning_only_touches_grounded_answers() -> None:
    refusal = Answer(kind="refusal", text_es=REFUSAL_ES, citations=())
    assert apply_safety_warning(refusal, "peligro eléctrico") is refusal


def test_expired_budget_raises_deadline_without_calling_the_llm() -> None:
    transport = FakeTransport(outcome=VALID_ANSWER_ES)

    with pytest.raises(DeadlineExceeded):
        generate(
            "¿cómo reviso la presión?",
            grounded_hits(),
            remaining_s=0.0,
            transport=transport,
        )

    assert transport.calls == []


def test_generation_that_outlives_the_budget_raises_deadline() -> None:
    def slow_transport(url, payload, timeout_s):
        time.sleep(0.05)
        return {"message": {"role": "assistant", "content": VALID_ANSWER_ES}}

    with pytest.raises(DeadlineExceeded):
        generate(
            "¿cómo reviso la presión?",
            grounded_hits(),
            remaining_s=0.01,
            transport=slow_transport,
        )


def test_transport_timeout_is_reported_as_a_deadline() -> None:
    with pytest.raises(DeadlineExceeded):
        generate(
            "¿cómo reviso la presión?",
            grounded_hits(),
            transport=FakeTransport(outcome=TimeoutError("read timed out")),
        )


def test_unusable_llm_transport_raises_llm_error() -> None:
    with pytest.raises(LLMError):
        generate(
            "¿cómo reviso la presión?",
            grounded_hits(),
            transport=FakeTransport(outcome=OSError("connection refused")),
        )


def test_malformed_llm_response_raises_llm_error() -> None:
    def empty_response(url, payload, timeout_s):
        return {"done": True}

    with pytest.raises(LLMError):
        generate(
            "¿cómo reviso la presión?", grounded_hits(), transport=empty_response
        )


def test_blank_question_is_rejected_at_the_boundary() -> None:
    with pytest.raises(ValueError, match="question"):
        generate("   ", grounded_hits(), transport=FakeTransport())
