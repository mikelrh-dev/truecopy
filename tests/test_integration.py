"""End-to-end, hermetic integration test for the web query path.

It exercises the same call sequence the handoff specifies — ``submit()``,
worker loop calling ``run_once()``, alarm fast path, semantic search, and a
fake LLM transport — but with everything backed by in-memory doubles so the
suite stays green without Qdrant or Ollama running (matching the hermetic
rule from Packet B).

What this proves:
* The scheduler receives a query, reports Spanish queue position, and
  returns a grounded Spanish answer with citations when evidence exists.
* An unknown alarm code routes to the exact ``REFUSAL_ES`` with no LLM call.
* An out-of-corpus semantic query also routes to the exact ``REFUSAL_ES``.
* A timeout under budget is reported as a Spanish timeout answer, never a
  partial or invented one.

Conventions:
* English code/comments, Spanish strings only where the operator would read
  them (here: we assert ``REFUSAL_ES`` and queue-position helpers exactly).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from plant_rag import config
from plant_rag.models import Answer, Citation, REFUSAL_ES
from plant_rag.scheduling.scheduler import Scheduler
from plant_rag.retrieve.store import (
    ALARMS_ALIAS,
    TEXT_ALIAS,
)
from test_retrieve import _filter_matches, fuse_prefetches

# ``embed_question`` does not exist yet in the core; the integration test
# supplies its own fake embedding so the suite stays hermetic.
_FAKE_EMBED_VECTOR: list[float] = [0.1] * config.EMBED_DIMS


def embed_question(question: str) -> list[float]:
    # Deterministic dummy vector of the right dimensionality. Real embeddings
    # come from BGE-M3; this fake exists only so integration tests stay green
    # without a model loaded.
    return list(_FAKE_EMBED_VECTOR)


# ---------------------------------------------------------------------------
# fake Qdrant client (enough surface for store + fastpath reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Point:
    id: Any
    payload: dict[str, Any] | None = None
    score: float = 1.0


@dataclass(frozen=True)
class _Alias:
    alias_name: str
    collection_name: str


@dataclass(frozen=True)
class _Collection:
    name: str


@dataclass
class _QueryResponse:
    points: list[_Point]


class FakeQdrantClient:
    """Minimal Qdrant double that satisfies ``store`` + ``fastpath`` reads.

    It holds in-memory point sets for two collections
    (``manual_r1_text``, ``manual_r1_alarms``) and the two current aliases.
    It intentionally does NOT pretend to be a real network client — it only
    implements the methods the query path actually calls.
    """

    def __init__(self) -> None:
        self._text_points: list[_Point] = []
        self._alarm_points: list[_Point] = []

    # collection metadata ---------------------------------------------------

    def get_collections(self) -> Any:
        return _CollectionsStub(
            [
                _Collection("manual_r1_text"),
                _Collection("manual_r1_alarms"),
            ]
        )

    def get_aliases(self) -> Any:
        return _AliasesStub(
            [
                _Alias(TEXT_ALIAS, "manual_r1_text"),
                _Alias(ALARMS_ALIAS, "manual_r1_alarms"),
            ]
        )

    # text search -----------------------------------------------------------

    def query_points(
        self,
        collection_name: str,
        query: Sequence[float] | None = None,
        *,
        limit: int,
        query_filter: Any = None,
        with_payload: bool = True,
        with_vectors: bool = False,
        prefetch: Any = None,
        timeout: float | None = None,
        score_threshold: float | None = None,
    ) -> _QueryResponse:
        if collection_name != "manual_r1_text":
            return _QueryResponse(points=[])

        if prefetch is not None:
            # Hybrid path: per-branch filtering + RRF (shared emulation from
            # test_retrieve, so both fakes answer prefetches identically).
            entries = [(p.id, p.payload, p.score) for p in self._text_points]
            order = fuse_prefetches(entries, prefetch, limit)
            by_id = {entry[0]: entry for entry in entries}
            return _QueryResponse(
                points=[
                    _Point(id=point_id, payload=by_id[point_id][1], score=by_id[point_id][2])
                    for point_id in order
                ]
            )

        scored = sorted(self._text_points, key=lambda p: -p.score)
        if score_threshold is not None:
            # A plain (non-prefetch) query honors score_threshold on the real
            # server too; mirror it so the pool-level floor is exercised.
            scored = [point for point in scored if point.score >= score_threshold]
        return _QueryResponse(points=scored[:limit])

    # alarm scroll -----------------------------------------------------------

    def scroll(
        self,
        collection_name: str,
        scroll_filter: Any = None,
        limit: int = 10,
        offset: Any = None,
        with_payload: bool = True,
        with_vectors: bool = False,
    ) -> tuple[list[_Point], Any]:
        if collection_name != "manual_r1_alarms":
            return [], None
        # The filter is honored, not ignored: an alarm lookup is an EXACT
        # match, so a double that returns every row regardless would let an
        # unknown code "resolve" and quietly invert what the fastpath tests
        # mean. Shares the filter emulation with test_retrieve.
        matching = [
            point
            for point in self._alarm_points
            if _filter_matches(point.payload, scroll_filter)
        ]
        return matching[:limit], None

    # ingestion surface (used by setup helpers below) -----------------------

    def create_collection(self, name: str, **kwargs: Any) -> None:
        # no-op: the fake already "has" the two collections above.
        pass

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: Any = None,
        wait: bool = False,
    ) -> None:
        # no-op: alarm lookup does not depend on the index existing here.
        pass

    # helpers for test setup -----------------------------------------------

    def add_text_point(
        self,
        *,
        id: Any,
        manual_id: str,
        page: int,
        revision: str,
        child_text: str,
        parent_text: str,
        score: float = 1.0,
    ) -> None:
        self._text_points.append(
            _Point(
                id=id,
                payload={
                    "manual_id": manual_id,
                    "page": page,
                    "revision": revision,
                    "child_text": child_text,
                    "parent_text": parent_text,
                },
                score=score,
            )
        )

    def add_alarm_point(
        self,
        *,
        id: Any,
        manual_id: str,
        page: int,
        revision: str,
        alarm_code: str,
        text: str,
    ) -> None:
        self._alarm_points.append(
            _Point(
                id=id,
                payload={
                    "manual_id": manual_id,
                    "page": page,
                    "revision": revision,
                    "alarm_code": alarm_code,
                    "text": text,
                },
            )
        )


class _CollectionsStub:
    def __init__(self, collections: list[_Collection]) -> None:
        self.collections = collections


class _AliasesStub:
    def __init__(self, aliases: list[_Alias]) -> None:
        self.aliases = aliases


# ---------------------------------------------------------------------------
# fake LLM transport
# ---------------------------------------------------------------------------


class FakeTransport:
    """Says whatever we tell it to say, so integration tests control output."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def __call__(self, url: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        return self.response


def _ok_response(text: str, citations: tuple[Citation, ...]) -> dict[str, Any]:
    return {
        "message": {"content": text},
        "done": True,
    }


# ---------------------------------------------------------------------------
# helper: build a scheduler wired for integration tests
# ---------------------------------------------------------------------------


def _integration_scheduler(
    client: FakeQdrantClient,
    transport: FakeTransport,
) -> Scheduler:
    from plant_rag.answer.pipeline import answer_question

    def generate(question: str, remaining_s: float) -> Answer:
        return answer_question(
            question,
            remaining_s,
            client=client,
            transport=transport,
        )

    return Scheduler(generate)


# ---------------------------------------------------------------------------
# alarm path
# ---------------------------------------------------------------------------


def test_known_alarm_code_returns_cited_answer() -> None:
    client = FakeQdrantClient()
    client.add_alarm_point(
        id=1,
        manual_id="828D",
        page=12,
        revision="r1",
        alarm_code="1234",
        text="Presión de refrigerante fuera de rango.",
    )

    transport = FakeTransport(_ok_response("ignored by alarm path", ()))
    scheduler = _integration_scheduler(client, transport)

    position = scheduler.submit("1234")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "answer"
    assert answer.text_es == "Código de alarma 1234: Presión de refrigerante fuera de rango."
    assert answer.citations == (Citation("828D", 12),)


def test_unknown_alarm_code_returns_exact_refusal() -> None:
    # A corpus WITH an alarm table (ADR-003, untouched by the E3 gate): an
    # unknown code refuses exactly. The text point plus an answer-ready
    # transport are the proof that NO semantic fallback ran - had it run, this
    # would have come back as a cited answer instead of the refusal.
    client = FakeQdrantClient()
    client.add_alarm_point(
        id=1,
        manual_id="828D",
        page=12,
        revision="r1",
        alarm_code="1234",
        text="Presión de refrigerante fuera de rango.",
    )
    client.add_text_point(
        id=2,
        manual_id="828D",
        page=5,
        revision="r1",
        child_text="la línea 99999 del listado",
        parent_text="la línea 99999 del listado",
        score=0.9,
    )
    transport = FakeTransport(
        _ok_response(
            "Aparece en la página 5 [Fuente: 828D Página 5].",
            (Citation("828D", 5),),
        )
    )
    scheduler = _integration_scheduler(client, transport)

    position = scheduler.submit("99999")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == ()


def test_digits_on_an_alarm_free_corpus_answer_semantically() -> None:
    # E3: with no alarm table, "2024" is prose (a year, a page, an error code
    # in a listing), never a code the corpus could have indexed. Refusing it by
    # SHAPE is a recall failure, so the digits take the semantic path.
    client = FakeQdrantClient()
    client.add_text_point(
        id=1,
        manual_id="828D",
        page=5,
        revision="r1",
        child_text="modal year 2024 procedure",
        parent_text="modal year 2024 procedure",
        score=0.9,
    )
    transport = FakeTransport(
        _ok_response(
            "El procedimiento de 2024 está en la página 5 [Fuente: 828D Página 5].",
            (Citation("828D", 5),),
        )
    )
    scheduler = _integration_scheduler(client, transport)

    position = scheduler.submit("2024")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "answer"
    assert answer.citations == (Citation("828D", 5),)


def test_non_code_shaped_query_skips_alarm_path() -> None:
    client = FakeQdrantClient()
    client.add_text_point(
        id=1,
        manual_id="828D",
        page=5,
        revision="r1",
        child_text="some child",
        parent_text="some parent",
        score=1.0,
    )
    transport = FakeTransport(
        _ok_response(
            "Es un conducto de derivación [Fuente: 828D Página 5].",
            (Citation("828D", 5),),
        )
    )
    scheduler = _integration_scheduler(client, transport)

    position = scheduler.submit("¿qué es el bypasses?")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "answer"
    assert answer.text_es == "Es un conducto de derivación [Fuente: 828D Página 5]."
    assert answer.citations == (Citation("828D", 5),)


# ---------------------------------------------------------------------------
# semantic path: grounded answer, refusal, timeout
# ---------------------------------------------------------------------------


def test_semantic_answer_with_evidence_is_cited() -> None:
    client = FakeQdrantClient()
    client.add_text_point(
        id=1,
        manual_id="828D",
        page=7,
        revision="r1",
        child_text="child",
        parent_text="parent",
        score=0.9,
    )
    transport = FakeTransport(
        _ok_response(
            "La presión de servicio es 1.2 bar [Fuente: 828D Página 7].",
            (Citation("828D", 7),),
        )
    )
    scheduler = _integration_scheduler(client, transport)

    position = scheduler.submit("¿cuál es la presión de servicio?")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "answer"
    assert answer.text_es == "La presión de servicio es 1.2 bar [Fuente: 828D Página 7]."
    assert answer.citations == (Citation("828D", 7),)


def test_out_of_corpus_question_returns_exact_refusal() -> None:
    client = FakeQdrantClient()
    scheduler = _integration_scheduler(client, FakeTransport({}))

    position = scheduler.submit("¿cuál es el teléfono de soporte?")
    answer = scheduler.run_once()

    assert position == 1
    assert answer is not None
    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == ()


def test_no_evidence_means_no_llm_call() -> None:
    client = FakeQdrantClient()
    transport = FakeTransport(_ok_response("should not be reached", ()))
    scheduler = _integration_scheduler(client, transport)

    scheduler.submit("¿quién es el presidente de francia?")
    answer = scheduler.run_once()

    assert answer is not None
    assert answer.kind == "refusal"
    assert answer.text_es == REFUSAL_ES
    assert answer.citations == ()


# ---------------------------------------------------------------------------
# timeout path: scheduler deadline
# ---------------------------------------------------------------------------


def test_expired_query_returns_spanish_timeout_not_partial_answer() -> None:
    class FakeClock:
        def __init__(self) -> None:
            self._now = 0.0

        def __call__(self) -> float:
            return self._now

        def advance(self, seconds: float) -> None:
            self._now += seconds

    clock = FakeClock()

    def immediate_answer(question: str, remaining_s: float) -> Answer:
        return Answer(
            kind="answer",
            text_es="partial answer that came too late",
            citations=(Citation("828D", 1),),
        )

    scheduler = Scheduler(immediate_answer, clock=clock)
    scheduler.submit("algo lento")

    # Advance past the deadline so the scheduler times out the entry before
    # dispatching to generation (ADR-005).
    clock.advance(config.QUERY_DEADLINE_S + 1.0)
    answer = scheduler.run_once()

    assert answer is not None
    assert answer.kind == "timeout"
    assert answer.citations == ()
    assert "no se entrega ninguna respuesta parcial" in answer.text_es
    assert "posición 1" in answer.text_es
