"""Pipeline wiring tests: expansion feeds retrieval only (never the answer).

Also home to the answer-cache seam tests: hit/miss/store semantics around
``answer_question(..., cache=...)`` with the chain stubbed at its seams.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plant_rag import config
from plant_rag.answer import pipeline
from plant_rag.answer.cache import AnswerCache
from plant_rag.answer.llm import DeadlineExceeded
from plant_rag.monitoring import QueryLog

REPO_ROOT = Path(__file__).resolve().parents[1]


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


# ---------------------------------------------------------------------------
# answer-cache seam (answer-cache spec: hit, miss-then-store, isolation)
# ---------------------------------------------------------------------------

_QUESTION = "¿Qué es el TIA?"


def _stub_empty_chain(monkeypatch, calls: list[str] | None = None) -> None:
    """The real chain with retrieval returning nothing: exact refusal, no LLM.

    ``lookup`` and ``keyword_variant`` are pinned so exactly ONE chain run
    appends exactly one question to ``calls`` (the observable "did the chain
    run?" counter for every seam test below).
    """
    monkeypatch.setattr(pipeline, "lookup", lambda client, question: None)
    monkeypatch.setattr(pipeline, "keyword_variant", lambda question: None)

    def _hybrid(client, vector, question, **kw):
        if calls is not None:
            calls.append(question)
        return ()

    monkeypatch.setattr(pipeline, "hybrid_search", _hybrid)


def _forbid_chain(monkeypatch) -> None:
    """Any chain activity after a cache hit is a bug."""

    def _boom(*args, **kwargs):
        raise AssertionError("the answering chain must not run on a cache hit")

    monkeypatch.setattr(pipeline, "lookup", _boom)
    monkeypatch.setattr(pipeline, "hybrid_search", _boom)


def test_repeat_question_hits_the_cache_logged_as_near_zero_latency(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    _stub_empty_chain(monkeypatch, calls)
    monkeypatch.setattr(pipeline, "current_revision", lambda client: 7)
    cache = AnswerCache(tmp_path / "cache.jsonl")
    log = QueryLog(tmp_path / "queries.jsonl")

    first = pipeline.answer_question(
        _QUESTION, 30.0, client=_NoopClient(), log=log, cache=cache
    )
    assert calls == [_QUESTION]  # miss: the chain ran once

    _forbid_chain(monkeypatch)
    second = pipeline.answer_question(
        "que es el tia", 30.0, client=_NoopClient(), log=log, cache=cache
    )

    assert second == first  # the stored answer, byte-identical
    assert calls == [_QUESTION]  # a normalized variant: still no chain run
    records = log.records()
    assert len(records) == 2
    assert records[1].latency_s < 0.1  # the hit's REAL near-zero latency


def test_a_revision_or_model_change_misses_and_reruns_the_chain(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    _stub_empty_chain(monkeypatch, calls)
    current = {"revision": 7}
    monkeypatch.setattr(pipeline, "current_revision", lambda client: current["revision"])
    cache = AnswerCache(tmp_path / "cache.jsonl")

    pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)
    assert calls == [_QUESTION]  # miss -> stored under (7, default model)

    pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)
    assert calls == [_QUESTION]  # same key: hit, the chain stays silent

    monkeypatch.setattr(config, "OLLAMA_MODEL", "other-model")
    pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)
    assert calls == [_QUESTION, _QUESTION]  # model swap: miss, chain again

    current["revision"] = 8
    pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)
    assert calls == [_QUESTION, _QUESTION, _QUESTION]  # reindex: miss again


def test_a_revision_swap_mid_answer_skips_the_store(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_empty_chain(monkeypatch, calls)
    revisions = iter([5, 6])  # pre-lookup says 5; after the chain the alias moved
    monkeypatch.setattr(pipeline, "current_revision", lambda client: next(revisions))
    cache = AnswerCache(tmp_path / "cache.jsonl")

    answer = pipeline.answer_question(
        _QUESTION, 30.0, client=_NoopClient(), cache=cache
    )

    assert answer.kind == "refusal"  # the produced answer is still delivered
    assert calls == [_QUESTION]
    assert cache.lookup(5, config.OLLAMA_MODEL, _QUESTION) is None
    assert cache.lookup(6, config.OLLAMA_MODEL, _QUESTION) is None
    assert not (tmp_path / "cache.jsonl").exists()  # nothing was stored


def test_a_timeout_is_never_stored(monkeypatch, tmp_path) -> None:
    _stub_empty_chain(monkeypatch)
    monkeypatch.setattr(pipeline, "current_revision", lambda client: 7)

    def _timeout(question, hits, **kwargs):
        raise DeadlineExceeded("the budget ran out")

    monkeypatch.setattr(pipeline, "llm_generate", _timeout)
    cache = AnswerCache(tmp_path / "cache.jsonl")

    with pytest.raises(DeadlineExceeded):
        pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)

    assert not (tmp_path / "cache.jsonl").exists()  # timeouts never cached


def test_a_failing_cache_store_never_breaks_the_answer(monkeypatch, tmp_path) -> None:
    _stub_empty_chain(monkeypatch)
    monkeypatch.setattr(pipeline, "current_revision", lambda client: 7)
    # A file where the parent directory should be: the append cannot succeed.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = AnswerCache(blocked / "cache.jsonl")

    answer = pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)

    assert answer.kind == "refusal"  # delivered normally
    assert not (blocked / "cache.jsonl").exists()


def test_a_raising_cache_store_is_swallowed_by_the_seam(monkeypatch, tmp_path) -> None:
    _stub_empty_chain(monkeypatch)
    monkeypatch.setattr(pipeline, "current_revision", lambda client: 7)
    cache = AnswerCache(tmp_path / "cache.jsonl")

    def _explode(revision, model, question, answer):
        raise OSError("disk full")

    monkeypatch.setattr(cache, "store", _explode)

    answer = pipeline.answer_question(_QUESTION, 30.0, client=_NoopClient(), cache=cache)

    assert answer.kind == "refusal"  # a broken cache seam never surfaces


def test_cache_none_never_resolves_revisions_or_touches_a_cache(monkeypatch) -> None:
    """Eval isolation: the default seam reads and writes nothing at all."""
    _stub_empty_chain(monkeypatch)

    def _boom(client):
        raise AssertionError("harness traffic must never resolve a revision")

    monkeypatch.setattr(pipeline, "current_revision", _boom)

    answer = pipeline.answer_question("presion neumaticos", 30.0, client=_NoopClient())

    assert answer.kind == "refusal"  # full chain, zero cache activity


def test_the_cache_seam_defaults_to_none_and_the_eval_never_passes_one() -> None:
    import inspect

    parameters = inspect.signature(pipeline.answer_question).parameters
    assert parameters["cache"].default is None

    # The eval entry point stays untouched by the answer-cache feature
    # (answering delta: "scripts/eval.py SHALL remain unmodified").
    eval_source = (REPO_ROOT / "scripts" / "eval.py").read_text(encoding="utf-8")
    assert "AnswerCache" not in eval_source
    assert "cache=" not in eval_source


def test_a_blank_question_raises_before_any_cache_lookup(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl")

    with pytest.raises(ValueError):
        pipeline.answer_question("   ", 30.0, client=_NoopClient(), cache=cache)

    assert not (tmp_path / "cache.jsonl").exists()
