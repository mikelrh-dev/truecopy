"""Pipeline wiring tests: expansion feeds retrieval only (never the answer)."""

from __future__ import annotations

from plant_rag import config
from plant_rag.answer import pipeline


class _NoopClient:
    """Never queried: hybrid and alarm lookups are patched in these tests."""


def test_pipeline_embeds_expanded_text_but_reranks_the_original_question(
    monkeypatch,
) -> None:
    embedded: list[str] = []
    hybrid_questions: list[str] = []
    rerank_questions: list[str] = []

    class RecordingEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            embedded.extend(texts)
            return [[0.1] * config.EMBED_DIMS for _ in texts]

    monkeypatch.setattr(pipeline, "get_embedder", lambda: RecordingEmbedder())
    monkeypatch.setattr(
        pipeline,
        "hybrid_search",
        lambda client, vector, question, **kw: hybrid_questions.append(question) or (),
    )
    monkeypatch.setattr(
        pipeline,
        "rerank",
        lambda question, hits, *, top_k=config.TOP_K, **kw: (
            rerank_questions.append(question) or tuple(hits)[:top_k]
        ),
    )

    answer = pipeline.answer_question("presion neumaticos", 30.0, client=_NoopClient())

    # Retrieval sees the expansion: the Spanish question plus the English
    # terms the manual actually uses.
    assert embedded == ["presion neumaticos tires tyres pressure"]
    assert hybrid_questions == [embedded[0]]
    # Ranking keeps the operator's wording.
    assert rerank_questions == ["presion neumaticos"]
    # Empty pool -> the exact refusal, zero network (the transport is never
    # even constructed), which is what this wiring test must not disturb.
    assert answer.kind == "refusal"


class _FakeEmbedder:
    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * config.EMBED_DIMS for _ in texts]


def _hits(pages: tuple[int, ...]) -> tuple:
    from plant_rag.models import Hit

    return tuple(
        Hit(manual_id="M", page=p, revision="r1", child=f"c{p}", parent=f"p{p}")
        for p in pages
    )


def test_keyword_variant_only_backfills_when_the_question_pool_is_thin(
    monkeypatch,
) -> None:
    """The variant is a recall aid: it fills free slots, never displaces.

    Regression guard from a live failure: ranking the whole union at once let
    variant candidates crowd out pages the question had already ranked right
    ("simbolos de luz" lost the indicator-lights page).
    """
    monkeypatch.setattr(pipeline, "get_embedder", lambda: _FakeEmbedder())
    calls: list[str] = []
    pool_for_call = [_hits((10, 15, 123, 141, 187)), _hits((300, 301, 302))]

    def fake_retrieve_pool(client, question, vector):
        calls.append(question)
        return pool_for_call[len(calls) - 1]

    monkeypatch.setattr(pipeline, "retrieve_pool", fake_retrieve_pool)
    # The rerank double preserves INCOMING order, making backfill observable.
    monkeypatch.setattr(
        pipeline, "rerank", lambda q, hits, *, top_k=5, **kw: tuple(hits)[:top_k]
    )

    # 1) A full primary pool: the variant never runs, nothing is displaced.
    hits = pipeline.retrieve_and_rank(_NoopClient(), "que simbolos de luz hay?")
    assert [h.page for h in hits] == [10, 15, 123, 141, 187]
    assert len(calls) == 1  # no second query when the question pool is full

    # 2) A thin primary pool (2 of 5): the variant backfills the free slots,
    #    strictly AFTER the question's own hits.
    calls.clear()
    pool_for_call[0] = _hits((10, 15))
    hits = pipeline.retrieve_and_rank(_NoopClient(), "que simbolos de luz hay?")
    assert [h.page for h in hits] == [10, 15, 300, 301, 302]
    assert len(calls) == 2  # the variant query did run

    # 3) A keyword question: the variant IS the question, so it never runs.
    calls.clear()
    hits = pipeline.retrieve_and_rank(_NoopClient(), "presion neumaticos")
    assert len(calls) == 1
