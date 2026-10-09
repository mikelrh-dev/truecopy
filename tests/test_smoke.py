"""Foundation smoke tests (Phase 1, Work Unit 1).

These prove the project skeleton is wired correctly: package imports from the
src layout, configuration defaults stay local-only, and the answer contract
constants match the specs exactly. Qdrant itself is exercised by the runtime
harness (``docker compose up``), not here.
"""

from dataclasses import FrozenInstanceError

import pytest

from plant_rag import config
from plant_rag.models import REFUSAL_ES, Hit


def test_config_defaults_point_at_local_services() -> None:
    assert config.QDRANT_URL == "http://127.0.0.1:6333"
    assert config.OLLAMA_URL == "http://127.0.0.1:11434"
    assert config.OLLAMA_MODEL == "qwen2.5:7b-instruct"
    assert config.BATCH_PAGES == 100


def test_config_rejects_invalid_integer_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANT_RAG_BATCH_PAGES", "abc")
    with pytest.raises(ValueError, match="PLANT_RAG_BATCH_PAGES"):
        config._env_int("BATCH_PAGES", 100)


def test_hit_is_frozen_and_keeps_provenance() -> None:
    hit = Hit(manual_id="828D", page=42, revision="r1", child="c1", parent="p1")
    assert (hit.manual_id, hit.page, hit.revision) == ("828D", 42, "r1")
    with pytest.raises(FrozenInstanceError):
        hit.page = 1  # type: ignore[misc]


def test_hit_rejects_invalid_provenance() -> None:
    with pytest.raises(ValueError, match="manual_id"):
        Hit(manual_id="", page=1, revision="r1", child="c", parent="p")
    with pytest.raises(ValueError, match="page"):
        Hit(manual_id="828D", page=0, revision="r1", child="c", parent="p")
    with pytest.raises(ValueError, match="revision"):
        Hit(manual_id="828D", page=1, revision=" ", child="c", parent="p")


def test_refusal_matches_answering_spec_exactly() -> None:
    # answering spec: "Exact not-found refusal" — byte-for-byte match.
    assert REFUSAL_ES == "No aparece en la documentación"
