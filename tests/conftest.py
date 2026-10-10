"""Shared hermetic fixtures: the answering pipeline never loads a model here.

Two seams are patched AUTOMATICALLY for every test because ``answer_question``
would otherwise load real checkpoints:

* ``pipeline.get_embedder`` -> a deterministic, dimension-correct double.
  BGE-M3 costs seconds and ~2.5GB of RAM (ADR-002); a suite that must run
  offline and fast cannot pay that per session.
* ``pipeline.rerank`` -> a retrieval-order cut to ``top_k``. The CPU
  cross-encoder (~2.3GB) has its own unit coverage with an injected scorer
  in ``tests/test_retrieve.py``; the real weights load only through the
  live ``scripts/eval.py`` - never by accident here.

Tests that need different behavior override these seams with their own
``monkeypatch.setattr(pipeline, ...)``; the real models stay behind the live
eval and the recorded runtime harness, not pytest.
"""

from __future__ import annotations

import os

# Before anything imports config: the web app's model pre-warm must stay off
# under test. ``tests/test_owner_ops.py`` drives the real ``web/app.py`` through
# Streamlit's AppTest, which would otherwise load BGE-M3 + the cross-encoder and
# POST to Ollama inside the suite — breaking the hermetic rule the whole suite
# is built on (it must pass with no services and no 2GB checkpoints).
os.environ["PLANT_RAG_PREWARM_MODELS"] = "0"

import pytest  # noqa: E402 - the env var above must be set before the import

from plant_rag import config  # noqa: E402
from plant_rag.answer import pipeline  # noqa: E402


class FakeEmbedder:
    """Stand-in for ``Embedder``: deterministic vectors, right dimensionality."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] * config.EMBED_DIMS for text in texts]


def _retrieval_order_rerank(
    question: str, hits, *, top_k: int = config.TOP_K, **_kwargs
):
    """``pipeline.rerank`` double: keep retrieval order, cut to ``top_k``."""
    return tuple(hits)[:top_k]


@pytest.fixture(autouse=True)
def hermetic_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Point the pipeline's heavy seams at doubles for one test."""
    monkeypatch.setattr(pipeline, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(pipeline, "rerank", _retrieval_order_rerank)
    return pipeline
