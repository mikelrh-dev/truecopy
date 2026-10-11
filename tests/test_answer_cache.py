"""Answer-cache unit tests: key normalization, JSONL round-trip, LRU, tolerance.

Hermetic by construction: every cache lives under pytest ``tmp_path``, and no
test reaches Qdrant, Ollama, or the real ``logs/`` directory.
"""

from __future__ import annotations

import json

import pytest

from plant_rag import config
from plant_rag.answer.cache import AnswerCache, normalize
from plant_rag.models import REFUSAL_ES, Answer, Citation


def _answer(
    text: str = "Código de alarma 1192: emergency stop.", pages: tuple[int, ...] = (12,)
) -> Answer:
    return Answer(
        kind="answer",
        text_es=text,
        citations=tuple(Citation(manual_id="M1", page=page) for page in pages),
    )


def _refusal() -> Answer:
    return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())


# ---------------------------------------------------------------------------
# normalize: deterministic key form
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("¿Qué es el TIA?", "que es el tia"),
        ("que es el tia", "que es el tia"),
        ("  ¿QUE   ES el  TIA!?  ", "que es el tia"),
        ("¡Bypass!", "bypass"),
        ("señal de alarma", "senal de alarma"),
        ("CÓDIGO 1192", "codigo 1192"),
        ("presión (neumáticos)", "presion (neumaticos)"),
        ("¿Qué significa... 'bypass'?", "que significa... 'bypass'"),
        ("", ""),
    ],
)
def test_normalize_collapses_operator_variants_onto_one_key(
    raw: str, expected: str
) -> None:
    assert normalize(raw) == expected


def test_normalize_is_idempotent_and_keeps_mid_question_punctuation() -> None:
    raw = "¿qué es el bypass?"
    once = normalize(raw)
    assert once == normalize(once) == "que es el bypass"
    # An inner separator survives so two different questions stay different.
    assert normalize("1192 o 1193?") != normalize("1192")


# ---------------------------------------------------------------------------
# round-trip and key scoping
# ---------------------------------------------------------------------------


def test_store_then_lookup_returns_the_byte_identical_answer(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl")
    answer = _answer(pages=(12, 40))

    cache.store(3, "model-a", "¿Qué es el TIA?", answer)

    assert cache.lookup(3, "model-a", "¿Qué es el TIA?") == answer
    # A normalized variant shares the very same entry.
    assert cache.lookup(3, "model-a", "que es el tia") == answer


def test_refusals_round_trip_verbatim(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl")

    cache.store(1, "model-a", "algo que no cubre el manual", _refusal())

    assert cache.lookup(1, "model-a", "algo que no cubre el manual") == _refusal()


def test_a_miss_returns_none_for_revision_or_model_mismatch(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl")
    cache.store(3, "model-a", "¿Qué es el TIA?", _answer())

    assert cache.lookup(4, "model-a", "¿Qué es el TIA?") is None  # reindexed
    assert cache.lookup(3, "model-b", "¿Qué es el TIA?") is None  # model swap
    assert cache.lookup(3, "model-a", "otra pregunta") is None  # new question


def test_entries_survive_a_reload_from_the_file(tmp_path) -> None:
    path = tmp_path / "cache.jsonl"
    answer = _answer()
    AnswerCache(path).store(3, "model-a", "¿Qué es el TIA?", answer)

    reloaded = AnswerCache(path)

    assert reloaded.lookup(3, "model-a", "¿Qué es el TIA?") == answer


# ---------------------------------------------------------------------------
# LRU cap
# ---------------------------------------------------------------------------


def test_the_in_memory_index_keeps_only_the_newest_entries(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl", max_entries=2)

    cache.store(1, "m", "primera pregunta", _answer("uno"))
    cache.store(1, "m", "segunda pregunta", _answer("dos"))
    cache.store(1, "m", "tercera pregunta", _answer("tres"))

    assert cache.lookup(1, "m", "primera pregunta") is None  # evicted
    assert cache.lookup(1, "m", "segunda pregunta") is not None
    assert cache.lookup(1, "m", "tercera pregunta") is not None


def test_a_hit_refreshes_recency_so_hot_entries_survive_eviction(tmp_path) -> None:
    cache = AnswerCache(tmp_path / "cache.jsonl", max_entries=2)

    cache.store(1, "m", "primera pregunta", _answer("uno"))
    cache.store(1, "m", "segunda pregunta", _answer("dos"))
    assert cache.lookup(1, "m", "primera pregunta") is not None  # touch "primera"
    cache.store(1, "m", "tercera pregunta", _answer("tres"))

    assert cache.lookup(1, "m", "segunda pregunta") is None  # least recent went
    assert cache.lookup(1, "m", "primera pregunta") is not None
    assert cache.lookup(1, "m", "tercera pregunta") is not None


def test_reload_trims_to_the_newest_cap(tmp_path) -> None:
    path = tmp_path / "cache.jsonl"
    writer = AnswerCache(path, max_entries=10)
    for index in range(5):
        writer.store(1, "m", f"pregunta {index}", _answer(str(index)))

    reloaded = AnswerCache(path, max_entries=2)

    assert reloaded.lookup(1, "m", "pregunta 3") is not None
    assert reloaded.lookup(1, "m", "pregunta 4") is not None
    assert reloaded.lookup(1, "m", "pregunta 0") is None


def test_a_config_change_invalidates_prior_entries(tmp_path, monkeypatch) -> None:
    """assumption A2: the key carries a config digest, so a change misses."""
    from plant_rag.answer import cache as cache_mod

    path = tmp_path / "cache.jsonl"
    cached = AnswerCache(path)
    cached.store(3, "model-a", "¿Qué es el TIA?", _answer())
    assert cached.lookup(3, "model-a", "¿Qué es el TIA?") == _answer()

    # An answer-relevant config change moves the fingerprint; a fresh cache on
    # the same file must treat every prior line as invalid (no manual wipe).
    monkeypatch.setattr(cache_mod, "_CONFIG_FINGERPRINT", "different-config")
    fresh = AnswerCache(path)

    assert fresh.lookup(3, "model-a", "¿Qué es el TIA?") is None


# ---------------------------------------------------------------------------
# tolerance: corrupt records, unreadable file, failed writes
# ---------------------------------------------------------------------------


def test_corrupt_records_are_skipped_and_valid_ones_keep_serving(tmp_path) -> None:
    path = tmp_path / "cache.jsonl"
    good = _answer()
    AnswerCache(path).store(1, "m", "¿Qué es el TIA?", good)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("this is not json\n")
        handle.write('{"revision": 1, "model": "m", "question": "a"}\n')  # truncated
        handle.write(
            json.dumps(
                {
                    "revision": 1,
                    "model": "m",
                    "question": "respuesta vacia",
                    "kind": "answer",  # an answer that cites nothing: invalid
                    "text_es": "x",
                    "citations": [],
                }
            )
            + "\n"
        )
    second = _answer("otra")
    AnswerCache(path).store(1, "m", "otra pregunta", second)

    cache = AnswerCache(path)

    assert cache.lookup(1, "m", "¿Qué es el TIA?") == good
    assert cache.lookup(1, "m", "otra pregunta") == second


def test_an_unreadable_file_reads_as_an_empty_cache(tmp_path, monkeypatch) -> None:
    import pathlib

    path = tmp_path / "cache.jsonl"
    AnswerCache(path).store(1, "m", "pregunta", _answer())

    real_open = pathlib.Path.open

    def failing_open(self, *args, **kwargs):
        if self == path:
            raise OSError("disk on fire")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", failing_open)

    assert AnswerCache(path).lookup(1, "m", "pregunta") is None


def test_a_failed_store_never_raises_and_leaves_no_trace(tmp_path) -> None:
    # A file where the parent directory should be: mkdir cannot succeed.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")
    cache = AnswerCache(blocked / "cache.jsonl")

    cache.store(1, "m", "pregunta", _answer())  # must not raise

    assert cache.lookup(1, "m", "pregunta") is None
    assert not (blocked / "cache.jsonl").exists()


# ---------------------------------------------------------------------------
# from_config
# ---------------------------------------------------------------------------


def test_from_config_is_none_when_the_cache_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(config, "ANSWER_CACHE_PATH", "")

    assert AnswerCache.from_config() is None


def test_from_config_reads_the_configured_path_and_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config, "ANSWER_CACHE_PATH", str(tmp_path / "cache.jsonl"))
    monkeypatch.setattr(config, "ANSWER_CACHE_MAX_ENTRIES", 7)

    cache = AnswerCache.from_config()

    assert cache is not None
    assert cache.path == tmp_path / "cache.jsonl"
    assert cache.max_entries == 7
