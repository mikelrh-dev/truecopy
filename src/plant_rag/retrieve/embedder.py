"""BGE-M3 embeddings on CPU (ADR-002: fp32, VRAM stays reserved for Ollama).

Conventions established here:

* The model loads lazily on first ``encode``: importing this module — and
  every unit test in the suite — never downloads the ~2.3GB checkpoint or
  allocates the ~2.5GB fp32 runtime. Heavy work happens only when someone
  actually encodes.
* ``encode`` is a drop-in for ``ingest.reindex.EncodeFn``: a sequence of
  texts in, exactly one ``config.EMBED_DIMS``-long vector out per text, with
  the model's output validated at the boundary before any point is upserted
  or any query is sent (collections are typed by ``config.EMBED_DIMS``).
* Batching is delegated to sentence-transformers with a bounded batch size
  (``config.EMBED_BATCH_SIZE``), so a 100-page batch of children never becomes
  one giant forward pass inside the ADR-002 memory line.
"""

from __future__ import annotations

from collections.abc import Sequence

from plant_rag import config

# ADR-002 pin: fp32 on CPU, multilingual (the ES->EN bridge), 1024-dim output.
MODEL_NAME = "BAAI/bge-m3"


class Embedder:
    """Lazily loaded BGE-M3 running fp32 on the CPU.

    Args:
        model_name: Hugging Face model id (``MODEL_NAME`` by default).
        batch_size: Forward-pass batch size; must be >= 1.
        device: Torch device — CPU only, by design (ADR-002).
        model: Optional preloaded model (test double) with an
            ``encode(texts, batch_size=..., convert_to_numpy=...,
            show_progress_bar=..., normalize_embeddings=...)`` signature.
    """

    def __init__(
        self,
        *,
        model_name: str = MODEL_NAME,
        batch_size: int = config.EMBED_BATCH_SIZE,
        device: str = "cpu",
        model: object | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        if model is None and not model_name.strip():
            raise ValueError("model_name must not be empty")
        self._model_name = model_name
        self._batch_size = batch_size
        self._device = device
        self._model = model

    @property
    def is_loaded(self) -> bool:
        """Whether the checkpoint has actually been loaded (laziness probe)."""
        return self._model is not None

    @property
    def model(self) -> object:
        """The underlying model, downloaded and loaded on first access."""
        if self._model is None:
            # Imported here, not at module level: the import itself pulls in
            # torch, and loading the checkpoint must stay out of unit tests.
            from sentence_transformers import SentenceTransformer

            # Plain load: BAAI/bge-m3 ships pytorch_model.bin only, and the
            # transformers pin in requirements.txt keeps that torch.load path
            # working on the ADR-002-pinned torch 2.5.1+cpu.
            self._model = SentenceTransformer(self._model_name, device=self._device)
        return self._model

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed ``texts`` into exactly ``config.EMBED_DIMS`` dims each.

        Satisfies ``ingest.reindex.EncodeFn`` so the same instance seeds the
        corpus at ingestion and embeds operator queries at search time.

        Returns:
            One plain ``list[float]`` per input text, in input order.

        Raises:
            ValueError: if the model returns the wrong number of vectors or
                the wrong dimensionality — collections are typed by
                ``config.EMBED_DIMS``, so a mismatch must fail loudly before
                anything is written or queried.
        """
        if not texts:
            return []
        vectors = self.model.encode(  # type: ignore[attr-defined]
            list(texts),
            batch_size=self._batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
            # COSINE ranking in Qdrant is scale-invariant, so unit-norm
            # vectors would only cost extra CPU for no ranking benefit.
            normalize_embeddings=False,
        )
        if len(vectors) != len(texts):
            raise ValueError(
                f"model returned {len(vectors)} vectors for {len(texts)} texts"
            )
        encoded: list[list[float]] = []
        for index, vector in enumerate(vectors):
            if len(vector) != config.EMBED_DIMS:
                raise ValueError(
                    f"model returned {len(vector)} dims for text {index}, "
                    f"expected {config.EMBED_DIMS}"
                )
            encoded.append([float(value) for value in vector])
        return encoded


_EMBEDDER: Embedder | None = None


def get_embedder() -> Embedder:
    """Process-wide embedder instance.

    Loading BGE-M3 costs seconds and ~2.5GB of RAM (ADR-002): the serving
    process pays for it exactly once, while ingestion and query paths share
    the same model object through this accessor.
    """
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = Embedder()
    return _EMBEDDER
