"""Query-queue tests (Phase 5, Work Unit 5).

Hermetic by contract: a fake clock and a fake generator — no Ollama, no
wall-clock sleeps. Ordering is asserted through the order the generator is
asked to run, because that order IS the scheduler's observable behavior.
"""

from __future__ import annotations

import pytest

from plant_rag import config
from plant_rag.answer.llm import DeadlineExceeded
from plant_rag.models import REFUSAL_ES, Answer, Citation
from plant_rag.scheduling.scheduler import (
    AGED_AFTER_S,
    Scheduler,
    queue_position_es,
    timeout_answer,
)


# --- doubles ---------------------------------------------------------------


class FakeClock:
    """Deterministic monotonic time the tests drive explicitly."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingGenerate:
    """Generator double: records (query, remaining budget), replays answers."""

    def __init__(self, answer: Answer | None = None, on_call=None) -> None:
        self.calls: list[tuple[str, float]] = []
        self.answer = answer if answer is not None else grounded_answer()
        self.on_call = on_call
        self.reached = 0

    def __call__(self, query: str, remaining_s: float) -> Answer:
        self.calls.append((query, remaining_s))
        self.reached += 1
        if self.on_call is not None:
            self.on_call()
        return self.answer


class ExplodingGenerate(RecordingGenerate):
    """Generator that reports an exhausted budget instead of answering."""

    def __call__(self, query: str, remaining_s: float) -> Answer:
        self.calls.append((query, remaining_s))
        raise DeadlineExceeded("budget gone")


def grounded_answer() -> Answer:
    return Answer(
        kind="answer",
        text_es="La presión es 1.2 bar. [Fuente: 828D Página 12]",
        citations=(Citation("828D", 12),),
    )


def refusal_answer() -> Answer:
    return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())


def make_scheduler(generate=None, clock: FakeClock | None = None):
    clock = clock if clock is not None else FakeClock()
    generate = generate if generate is not None else RecordingGenerate()
    return Scheduler(generate, clock=clock), generate, clock


# --- 5.1 heap order: FIFO, safety preemption, aging -------------------------


def test_idle_scheduler_returns_none() -> None:
    scheduler, generate, _ = make_scheduler()

    assert scheduler.run_once() is None
    assert generate.calls == []
    assert scheduler.pending == 0


def test_submit_rejects_blank_queries() -> None:
    scheduler, _, _ = make_scheduler()
    with pytest.raises(ValueError, match="query"):
        scheduler.submit("   ")


def test_routine_queries_are_served_in_arrival_order() -> None:
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("primera consulta")
    scheduler.submit("segunda consulta")

    first = scheduler.run_once()
    clock.advance(5)
    second = scheduler.run_once()

    assert first is not None and second is not None
    assert [call[0] for call in generate.calls] == [
        "primera consulta",
        "segunda consulta",
    ]
    # The dispatch hands over exactly the budget the deadline has left (ADR-005).
    assert generate.calls[0][1] == pytest.approx(config.QUERY_DEADLINE_S)
    assert generate.calls[1][1] == pytest.approx(config.QUERY_DEADLINE_S - 5)


def test_safety_query_jumps_the_queue_and_keeps_relative_order() -> None:
    scheduler, generate, _ = make_scheduler()
    scheduler.submit("rutina uno")
    scheduler.submit("rutina dos")
    scheduler.submit("¿cuál es el riesgo de choque al purgar?")

    for _ in range(3):
        scheduler.run_once()

    # Query-queue spec: safety first, displaced queries keep relative order.
    assert [call[0] for call in generate.calls] == [
        "¿cuál es el riesgo de choque al purgar?",
        "rutina uno",
        "rutina dos",
    ]


def test_two_safety_queries_keep_arrival_order() -> None:
    scheduler, generate, _ = make_scheduler()
    scheduler.submit("peligro eléctrico primero")
    scheduler.submit("peligro eléctrico segundo")

    scheduler.run_once()
    scheduler.run_once()

    assert [call[0] for call in generate.calls] == [
        "peligro eléctrico primero",
        "peligro eléctrico segundo",
    ]


def test_aged_routine_beats_a_fresh_safety_query() -> None:
    # ADR-005 anti-starvation: wait >= 15s outranks even fresh safety.
    assert AGED_AFTER_S == 15.0
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("rutina esperada")
    clock.advance(AGED_AFTER_S)
    scheduler.submit("peligro recién llegado")

    scheduler.run_once()
    scheduler.run_once()

    assert [call[0] for call in generate.calls] == [
        "rutina esperada",
        "peligro recién llegado",
    ]


def test_wait_below_the_aging_threshold_stays_fresh() -> None:
    # The threshold is exactly ADR-005's 15s: before it, safety still wins.
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("rutina esperando")
    clock.advance(AGED_AFTER_S - 0.5)
    scheduler.submit("peligro recién llegado")

    scheduler.run_once()
    scheduler.run_once()

    assert [call[0] for call in generate.calls] == [
        "peligro recién llegado",
        "rutina esperando",
    ]


def test_position_is_reported_at_submit_in_spanish() -> None:
    scheduler, generate, _ = make_scheduler()

    assert scheduler.submit("una") == 1
    assert scheduler.submit("dos") == 2
    assert scheduler.submit("tres") == 3
    assert queue_position_es(2) == "Posición en la cola: 2"


def test_position_counts_the_query_being_generated() -> None:
    # A query joining during generation is second in line: one is in flight.
    scheduler_holder: dict = {}

    def join_during_generation() -> None:
        scheduler_holder["position"] = scheduler_holder["scheduler"].submit("segunda")

    generate = RecordingGenerate(on_call=join_during_generation)
    scheduler = Scheduler(generate, clock=FakeClock())
    scheduler_holder["scheduler"] = scheduler
    scheduler.submit("primera")

    scheduler.run_once()

    assert scheduler_holder["position"] == 2


# --- 5.2 deadline gates dispatch AND generation ----------------------------


def test_expired_entry_times_out_without_reaching_generation() -> None:
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("consulta que espera")

    clock.advance(config.QUERY_DEADLINE_S)
    answer = scheduler.run_once()

    assert answer is not None
    assert answer.kind == "timeout"
    assert answer.citations == ()  # never partial, never citing
    assert "posición 1" in answer.text_es
    assert f"{config.QUERY_DEADLINE_S} s" in answer.text_es
    assert "no se entrega ninguna respuesta parcial" in answer.text_es
    assert answer.text_es != REFUSAL_ES  # a timeout is not a refusal
    assert generate.calls == []


def test_timeout_reports_each_querys_submit_position() -> None:
    scheduler, _, clock = make_scheduler()
    scheduler.submit("una")
    scheduler.submit("dos")

    clock.advance(config.QUERY_DEADLINE_S)
    first = scheduler.run_once()
    second = scheduler.run_once()

    assert first is not None and "posición 1" in first.text_es
    assert second is not None and "posición 2" in second.text_es


def test_expired_entries_surface_before_any_ready_dispatch() -> None:
    # The timeout can never hide behind fresh traffic: purge runs first.
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("rutina vencida")
    clock.advance(config.QUERY_DEADLINE_S)
    scheduler.submit("peligro fresco")

    timeout = scheduler.run_once()
    served = scheduler.run_once()

    assert timeout is not None and timeout.kind == "timeout"
    assert served is not None and served.kind == "answer"
    assert [call[0] for call in generate.calls] == ["peligro fresco"]


def test_generation_budget_is_only_what_the_deadline_has_left() -> None:
    scheduler, generate, clock = make_scheduler()
    scheduler.submit("consulta")
    clock.advance(10)

    scheduler.run_once()

    assert generate.calls[0][1] == pytest.approx(config.QUERY_DEADLINE_S - 10)


def test_generator_reported_deadline_becomes_a_timeout() -> None:
    generate = ExplodingGenerate()
    scheduler = Scheduler(generate, clock=FakeClock())
    scheduler.submit("consulta")

    answer = scheduler.run_once()

    assert answer is not None
    assert answer.kind == "timeout"
    assert answer.citations == ()
    assert "posición 1" in answer.text_es


def test_late_answer_is_discarded_never_delivered() -> None:
    # A generator that outlives the deadline without reporting it hands back
    # a full answer — the scheduler must throw it away (ADR-005: never late).
    clock = FakeClock()

    def overrun_and_answer(query: str, remaining_s: float) -> Answer:
        clock.advance(remaining_s + 1)
        return grounded_answer()

    scheduler = Scheduler(overrun_and_answer, clock=clock)
    scheduler.submit("consulta")

    answer = scheduler.run_once()

    assert answer is not None
    assert answer.kind == "timeout"
    assert answer.citations == ()
    assert "[Fuente:" not in answer.text_es  # the late answer leaked nowhere


def test_refusal_answers_pass_through_untouched() -> None:
    scheduler, generate, _ = make_scheduler(generate=RecordingGenerate(refusal_answer()))
    scheduler.submit("pregunta sin respuesta")

    answer = scheduler.run_once()

    assert answer is not None
    assert (answer.kind, answer.text_es, answer.citations) == (
        "refusal",
        REFUSAL_ES,
        (),
    )


def test_timeout_answer_helper_is_spanish_and_uncited() -> None:
    answer = timeout_answer(position=3)

    assert answer.kind == "timeout"
    assert answer.citations == ()
    assert "posición 3" in answer.text_es
    assert "parcial" in answer.text_es


# --- queue continuity: a refusal opens the way, never blocks the next one ---


def test_a_refusal_does_not_block_the_next_question() -> None:
    # The operator asks out-of-corpus, then in-corpus: both resolve in order
    # through the same queue. A refusal must never wedge the scheduler.
    answers = {
        "¿quién fue el presidente en 1995?": refusal_answer(),
        "presion neumaticos": grounded_answer(),
    }
    generate = RecordingGenerate()
    generate.answer = None  # answered per question below

    def route(query: str, remaining_s: float) -> Answer:
        generate.calls.append((query, remaining_s))
        return answers[query]

    scheduler = Scheduler(route, clock=FakeClock())
    scheduler.submit("¿quién fue el presidente en 1995?")
    scheduler.submit("presion neumaticos")

    first = scheduler.run_once()
    second = scheduler.run_once()
    third = scheduler.run_once()

    assert first is not None and first.kind == "refusal"
    assert first.text_es == REFUSAL_ES
    assert second is not None and second.kind == "answer"
    assert third is None  # queue drained, ready for the next question
