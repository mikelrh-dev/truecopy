"""Retrieval tests (Phase 3, Work Unit 3).

Hermetic by contract: no model downloads, no running Qdrant, no real corpus.
BGE-M3 is replaced by a deterministic fake model (``embedder.Embedder`` loads
the real checkpoint lazily, so importing it is free), and Qdrant by an
in-memory fake mirroring exactly the calls ``store`` and ``fastpath`` make:
alias reads, ``query_points`` with a revision filter, and payload ``scroll``.
The real BGE-M3 + Qdrant path is exercised by the runtime harness reported
separately from this suite.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from qdrant_client.http.models import Distance, PayloadSchemaType, TokenizerType

from plant_rag import config
from plant_rag.models import Hit
from plant_rag.retrieve import store
from plant_rag.retrieve.embedder import MODEL_NAME, Embedder, get_embedder
from plant_rag.retrieve.fastpath import AlarmHit, alarm_code, lookup
from plant_rag.retrieve.store import current_target, revision_tag, search


# --- shared Qdrant emulation for filters and hybrid (prefetch + RRF) -------


_TOKEN_RE = re.compile(r"\w+")


def _text_tokens(text: str) -> list[str]:
    """Word-index tokens for a MatchText: case-folded, min length 2."""
    return [token for token in _TOKEN_RE.findall(text.casefold()) if len(token) >= 2]


def _condition_matches(payload: dict, condition) -> bool:
    match = condition.match
    if hasattr(match, "text"):
        # MatchText over the word payload index: ALL tokens must appear
        # (case-insensitive). An untokenizable query matches NOTHING - the
        # live server was probed and never degenerates to "match everything".
        tokens = _text_tokens(match.text)
        if not tokens:
            return False
        haystack = str(payload.get(condition.key) or "").casefold()
        return all(token in haystack for token in tokens)
    # MatchValue (revision scoping, alarm codes): exact payload equality.
    return payload.get(condition.key) == getattr(match, "value", None)


def _filter_matches(payload: dict, query_filter) -> bool:
    if query_filter is None:
        return True
    return all(_condition_matches(payload, condition) for condition in (query_filter.must or []))


def fuse_prefetches(
    entries: list[tuple[object, dict, float]], prefetches, limit: int
) -> list[object]:
    """Emulate Qdrant's per-prefetch filtering + ``Fusion.RRF`` ordering.

    ``entries`` are ``(id, payload, score)`` triples. Each prefetch filters
    (its own ``filter``, then its own ``score_threshold`` - the floor is
    per-branch and applied BEFORE fusion, exactly like the server), keeps
    ``prefetch.limit`` best-scored candidates, and the branches are merged
    with reciprocal-rank fusion: ``score = sum(1 / (60 + rank))``.
    """
    branches: list[list[object]] = []
    for prefetch in prefetches:
        matched = [
            entry
            for entry in entries
            if _filter_matches(entry[1], getattr(prefetch, "filter", None))
        ]
        threshold = getattr(prefetch, "score_threshold", None)
        if threshold is not None:
            matched = [entry for entry in matched if entry[2] >= threshold]
        matched.sort(key=lambda entry: -entry[2])  # relevance rank per branch
        branch_limit = getattr(prefetch, "limit", None) or len(matched)
        branches.append([entry[0] for entry in matched[:branch_limit]])

    fused: dict[object, float] = {}
    for branch in branches:
        for rank, point_id in enumerate(branch):
            fused[point_id] = fused.get(point_id, 0.0) + 1.0 / (60 + rank)
    ranked = sorted(fused.items(), key=lambda item: -item[1])
    return [point_id for point_id, _ in ranked[:limit]]


# --- doubles ---------------------------------------------------------------


class FakeModel:
    """sentence-transformers stand-in: deterministic vectors, records its call."""

    def __init__(self, dims: int = config.EMBED_DIMS) -> None:
        self.dims = dims
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        self.calls.append({"texts": texts, **kwargs})
        return [[float(len(text))] * self.dims for text in texts]


class FakeQdrant:
    """In-memory stand-in for exactly the Qdrant calls store/fastpath use.

    Alias resolution and payload filters are applied inside the fake (mirroring
    one Qdrant request), and every query/scroll call is recorded so tests can
    assert what the code asked for — including that the fast path never asks
    for a vector search at all.
    """

    def __init__(self) -> None:
        self.collections: dict[str, list[dict]] = {}
        self.collection_configs: dict[str, dict] = {}
        self.payload_indexes: list[dict] = []
        self.aliases: dict[str, str] = {}
        self.query_calls: list[dict] = []
        self.scroll_calls: list[dict] = []
        self._next_id = 0

    # --- client API -----------------------------------------------------

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=alias, collection_name=target)
                for alias, target in self.aliases.items()
            ]
        )

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.collections]
        )

    def create_collection(self, name, vectors_config=None, **kwargs):
        if name in self.collections:
            raise RuntimeError(f"collection {name} already exists")
        self.collections[name] = []
        self.collection_configs[name] = {"vectors_config": vectors_config, **kwargs}
        return True

    def create_payload_index(self, name, field_name=None, field_schema=None, wait=True):
        self.payload_indexes.append(
            {"collection": name, "field_name": field_name, "field_schema": field_schema}
        )
        return True

    def query_points(
        self,
        collection_name,
        query=None,
        limit=10,
        query_filter=None,
        with_payload=True,
        with_vectors=False,
        prefetch=None,
        timeout=None,
    ):
        self.query_calls.append(
            {
                "collection": self._resolve(collection_name),
                "query": query,
                "limit": limit,
                "filter": query_filter,
                "prefetch": prefetch,
            }
        )
        if prefetch is not None:
            # Hybrid path: per-branch filtering + RRF, then the final limit.
            entries = [
                (point["id"], point["payload"], point["score"])
                for point in self.collections.get(self._resolve(collection_name), [])
            ]
            order = fuse_prefetches(entries, prefetch, limit)
            by_id = {entry[0]: entry for entry in entries}
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id=point_id,
                        payload=by_id[point_id][1],
                        score=by_id[point_id][2],
                    )
                    for point_id in order
                ]
            )
        matching = self._matching(collection_name, query_filter)
        matching.sort(key=lambda point: -point["score"])
        matching = matching[:limit]
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id=point["id"], payload=point["payload"], score=point["score"]
                )
                for point in matching
            ]
        )

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=10,
        offset=None,
        with_payload=True,
        with_vectors=False,
    ):
        self.scroll_calls.append(
            {
                "collection": self._resolve(collection_name),
                "filter": scroll_filter,
                "limit": limit,
                "offset": offset,
            }
        )
        matching = self._matching(collection_name, scroll_filter)[:limit]
        records = [
            SimpleNamespace(id=point["id"], payload=point["payload"])
            for point in matching
        ]
        return records, None  # small fixture data: one page, always exhausted

    # --- test helpers (not part of the client API) ----------------------

    def seed(
        self,
        collection: str,
        *payloads: dict,
        scores: list[float] | None = None,
    ) -> None:
        """Insert payload-only points directly, bypassing ingestion.

        ``scores`` mirrors the payloads as ranked by a vector search; the
        default keeps every point above ``config.MIN_SCORE`` so existing
        tests see the whole seeded set.
        """
        if scores is not None and len(scores) != len(payloads):
            raise ValueError("scores must align with payloads")
        for index, payload in enumerate(payloads):
            self._next_id += 1
            score = 0.9 if scores is None else scores[index]
            self.collections.setdefault(collection, []).append(
                {
                    "id": f"seed-{self._next_id}",
                    "vector": {},
                    "payload": payload,
                    "score": score,
                }
            )

    def alias(self, alias: str, collection: str) -> None:
        """Point an alias at a collection without going through a swap."""
        if collection not in self.collections:
            self.collections[collection] = []
        self.aliases[alias] = collection

    # --- internals -------------------------------------------------------

    def _resolve(self, name: str) -> str:
        return self.aliases.get(name, name)

    def _matching(self, collection_name: str, query_filter) -> list[dict]:
        points = self.collections.get(self._resolve(collection_name), [])
        return [
            point for point in points if _filter_matches(point["payload"], query_filter)
        ]


def query_vector(value: float = 1.0) -> list[float]:
    """A vector of the exact dimensionality collections are typed with."""
    return [value] * config.EMBED_DIMS


def text_payload(**overrides) -> dict:
    payload = {
        "manual_id": "828D",
        "page": 3,
        "revision": "r1",
        "child_id": "828D:r1:p3:c0",
        "parent_id": "828D:r1:p3",
        "child_text": "Coolant pressure switch behaviour under load.",
        "parent_text": "Chapter 4. Coolant circuit — full page text.",
    }
    payload.update(overrides)
    return payload


def alarm_payload(**overrides) -> dict:
    payload = {
        "manual_id": "828D",
        "page": 7,
        "revision": "r1",
        "alarm_code": "12345",
        "text": "Coolant pressure switch open circuit",
    }
    payload.update(overrides)
    return payload


# --- 3.1 embedder: BGE-M3 fp32 CPU batched encoding ------------------------


def test_encode_returns_exactly_config_dims_per_text() -> None:
    model = FakeModel()
    embedder = Embedder(model=model)

    vectors = embedder.encode(["presión del refrigerante", "spindle warning"])

    assert config.EMBED_DIMS == 1024  # ADR-002 pin: collections type this
    assert len(vectors) == 2
    assert all(len(vector) == config.EMBED_DIMS for vector in vectors)
    assert all(isinstance(value, float) for vector in vectors for value in vector[:1])
    assert model.calls[0]["texts"] == [
        "presión del refrigerante",
        "spindle warning",
    ]


def test_encode_rejects_model_output_with_wrong_dims() -> None:
    embedder = Embedder(model=FakeModel(dims=3))

    with pytest.raises(ValueError, match="dims"):
        embedder.encode(["texto"])


def test_encode_rejects_model_output_with_wrong_vector_count() -> None:
    class DroppingModel(FakeModel):
        def encode(self, texts, **kwargs):
            return super().encode(texts[:1], **kwargs)

    embedder = Embedder(model=DroppingModel())

    with pytest.raises(ValueError, match="vectors"):
        embedder.encode(["a", "b"])


def test_encode_batches_with_config_batch_size_on_cpu() -> None:
    model = FakeModel()
    embedder = Embedder(model=model)

    embedder.encode(["one", "two", "three"])

    call = model.calls[0]
    assert call["batch_size"] == config.EMBED_BATCH_SIZE
    assert config.EMBED_BATCH_SIZE >= 1
    # One batched forward pass for the whole list, never one per text.
    assert len(model.calls) == 1
    assert call["convert_to_numpy"] is True
    assert call["show_progress_bar"] is False


def test_encode_empty_input_never_touches_the_model() -> None:
    embedder = Embedder()  # no fake model: a real load would download BGE-M3

    assert embedder.encode([]) == []
    assert embedder.is_loaded is False  # laziness held: nothing was imported


def test_embedder_rejects_invalid_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        Embedder(model=FakeModel(), batch_size=0)


def test_get_embedder_is_the_process_wide_instance() -> None:
    # One model per process: loading BGE-M3 costs seconds and ~2.5GB (ADR-002).
    assert get_embedder() is get_embedder()
    assert get_embedder().is_loaded is False  # accessor must not trigger a load


def test_embedder_ships_the_adr_002_model_name() -> None:
    assert MODEL_NAME == "BAAI/bge-m3"  # ADR-002: fp32 multilingual, 1024-dim
    embedder = Embedder(model=FakeModel())
    assert embedder.is_loaded is True  # injected model counts as loaded
    assert embedder.model is not None


# --- 3.2 store: current_* alias, revision filter, pages in Hit ------------


def test_create_text_collection_creates_on_disk_vectors() -> None:
    fake = FakeQdrant()

    store.create_text_collection(fake, "manual_r1_text")

    created = fake.collection_configs["manual_r1_text"]
    params = created["vectors_config"]
    assert params.size == config.EMBED_DIMS
    assert params.on_disk is True  # ADR-002: vectors out of resident RAM
    assert params.distance == Distance.COSINE


def test_create_text_collection_also_creates_the_child_text_index() -> None:
    """Every revision is hybrid-searchable from birth (text branch fix).

    Regression guard: r6 served dense-only text matching until its index was
    created by hand, because creation lived outside the ingest pipeline. A
    collection without this index silently loses the MatchText branch.
    """
    fake = FakeQdrant()

    store.create_text_collection(fake, "manual_r7_text")

    assert len(fake.payload_indexes) == 1
    index = fake.payload_indexes[0]
    assert index["collection"] == "manual_r7_text"
    assert index["field_name"] == "child_text"
    schema = index["field_schema"]
    assert schema == store.text_index_params()
    assert schema.tokenizer == TokenizerType.WORD
    assert schema.min_token_len == 2
    assert schema.lowercase is True


def test_create_alarm_collection_is_payload_only_with_code_index() -> None:
    fake = FakeQdrant()

    store.create_alarm_collection(fake, "manual_r1_alarms")

    # ADR-003: exact MatchValue never touches a vector, so none exist.
    assert fake.collection_configs["manual_r1_alarms"]["vectors_config"] == {}
    assert len(fake.payload_indexes) == 1
    index = fake.payload_indexes[0]
    assert index["collection"] == "manual_r1_alarms"
    assert index["field_name"] == "alarm_code"
    assert index["field_schema"] == PayloadSchemaType.KEYWORD


def test_search_maps_english_passages_with_pages_into_hits() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=12, child_text="Overheat alarm appears when...", revision="r1"),
        text_payload(page=48, child_text="Reset the traverse axis...", revision="r1"),
    )

    hits = search(fake, query_vector())

    assert all(isinstance(hit, Hit) for hit in hits)
    # Retrieval spec: every passage carries its page number.
    assert [hit.page for hit in hits] == [12, 48]
    assert [hit.child for hit in hits] == [
        "Overheat alarm appears when...",
        "Reset the traverse axis...",
    ]
    assert hits[0].parent.startswith("Chapter 4.")  # page-sized grounding text
    assert all(hit.revision == "r1" and hit.manual_id == "828D" for hit in hits)


def test_search_scopes_every_query_to_the_alias_revision() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    # A stray point inside the CURRENT collection carrying a stale revision:
    # only the per-query revision filter can keep it out of the answer.
    fake.seed(
        "manual_r1_text",
        text_payload(revision="r0", child_text="stale passage"),
        text_payload(revision="r1", child_text="current passage"),
    )

    hits = search(fake, query_vector())

    assert [hit.child for hit in hits] == ["current passage"]
    assert {hit.revision for hit in hits} == {"r1"}
    # The filter is derived from the alias target, not from the payload.
    sent = fake.query_calls[0]["filter"]
    assert sent.must[0].key == "revision"
    assert sent.must[0].match.value == "r1"
    assert fake.query_calls[0]["collection"] == "manual_r1_text"


def test_search_resolves_the_alias_not_a_physical_name() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r2_text")
    fake.seed("manual_r2_text", text_payload(revision="r2"))

    hits = search(fake, query_vector())

    assert len(hits) == 1 and hits[0].revision == "r2"
    assert fake.query_calls[0]["collection"] == "manual_r2_text"


def test_search_returns_empty_before_any_ingestion() -> None:
    fake = FakeQdrant()

    assert search(fake, query_vector()) == ()
    assert fake.query_calls == []  # no alias, no traffic


def test_search_returns_empty_for_out_of_corpus_queries() -> None:
    # Retrieval spec: "Query outside the corpus" — empty set, nothing synthesized.
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")

    assert search(fake, query_vector()) == ()


def test_search_drops_results_below_the_min_score_floor() -> None:
    # Calibration (batch-3 harness): in-corpus top hits 0.571/0.467 vs
    # out-of-corpus 0.365/0.314 -> MIN_SCORE=0.40 separates them. The weak
    # tail is not evidence and must never reach the prompt.
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=12),
        text_payload(page=48),
        scores=[0.571, 0.365],
    )

    hits = search(fake, query_vector())

    assert [hit.page for hit in hits] == [12]


def test_search_returns_empty_when_every_result_is_below_the_floor() -> None:
    # A wholly out-of-corpus query now yields the same () as an empty corpus,
    # so the answer gate refuses without an LLM call (ADR-004).
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=1),
        text_payload(page=2),
        scores=[0.365, 0.314],
    )

    assert search(fake, query_vector()) == ()


def test_search_respects_top_k() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(page=1),
        text_payload(page=2),
        text_payload(page=3),
    )

    hits = search(fake, query_vector(), top_k=2)

    assert [hit.page for hit in hits] == [1, 2]
    assert fake.query_calls[0]["limit"] == 2
    assert config.TOP_K == 5  # default keeps prompts inside the 8k context


def test_search_rejects_wrong_dimension_vectors() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")

    with pytest.raises(ValueError, match="dims"):
        search(fake, [0.1, 0.2, 0.3])


def test_search_fails_actionably_when_provenance_is_missing() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    payload = text_payload()
    del payload["parent_text"]
    fake.seed("manual_r1_text", payload)

    with pytest.raises(ValueError, match="parent_text"):
        search(fake, query_vector())


def test_search_rejects_invalid_top_k() -> None:
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")

    with pytest.raises(ValueError, match="top_k"):
        search(fake, query_vector(), top_k=0)


# --- 3.3 fastpath: exact MatchValue, never embeddings ---------------------


def test_known_alarm_resolves_exactly_with_its_page() -> None:
    fake = FakeQdrant()
    fake.alias("current_alarms", "manual_r1_alarms")
    fake.seed("manual_r1_alarms", alarm_payload(page=77))

    hits = lookup(fake, "12345")

    assert hits is not None and len(hits) == 1
    alarm = hits[0]
    assert isinstance(alarm, AlarmHit)
    assert (alarm.alarm_code, alarm.page, alarm.revision) == ("12345", 77, "r1")
    assert alarm.text == "Coolant pressure switch open circuit"
    # ADR-003: resolution never depends on embedding similarity.
    assert fake.query_calls == []
    assert len(fake.scroll_calls) == 1
    conditions = fake.scroll_calls[0]["filter"].must
    assert {(c.key, c.match.value) for c in conditions} == {
        ("alarm_code", "12345"),
        ("revision", "r1"),
    }


def test_unknown_alarm_code_returns_empty_for_the_refusal_path() -> None:
    fake = FakeQdrant()
    fake.alias("current_alarms", "manual_r1_alarms")
    fake.seed("manual_r1_alarms", alarm_payload())

    assert lookup(fake, "99999") == ()
    assert fake.query_calls == []  # unknown code still never touches vectors


def test_non_code_query_is_routed_away_from_the_fast_path() -> None:
    fake = FakeQdrant()
    fake.alias("current_alarms", "manual_r1_alarms")
    fake.seed("manual_r1_alarms", alarm_payload())

    # None (not a code) is the caller's signal to use semantic search; the
    # fast path must not have talked to the store at all.
    assert lookup(fake, "¿qué significa la alarma 12345?") is None
    assert lookup(fake, "") is None
    assert fake.scroll_calls == [] and fake.query_calls == []


def test_lookup_returns_empty_before_any_ingestion() -> None:
    assert lookup(FakeQdrant(), "12345") == ()


def test_alarm_lookup_never_returns_a_stale_revision() -> None:
    fake = FakeQdrant()
    fake.alias("current_alarms", "manual_r2_alarms")
    fake.seed(
        "manual_r2_alarms",
        alarm_payload(revision="r1", text="stale row"),
        alarm_payload(revision="r2", text="current row"),
    )

    hits = lookup(fake, "12345")

    assert hits is not None and [hit.text for hit in hits] == ["current row"]
    assert {hit.revision for hit in hits} == {"r2"}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("1234", "1234"),  # lower bound of the ingestion rule
        ("12345", "12345"),
        ("123456", "123456"),  # upper bound
        (" 2045 ", "2045"),  # trimmed, still exact
        ("123", None),  # too short
        ("1234567", None),  # too long
        ("1234a", None),  # not a code
        ("alarma 12345", None),  # sentence, not a code
        ("", None),
    ],
)
def test_alarm_code_shape_matches_the_ingestion_rule(query, expected) -> None:
    assert alarm_code(query) == expected


def test_alarm_hit_rejects_invalid_provenance() -> None:
    with pytest.raises(ValueError, match="page"):
        AlarmHit(
            manual_id="828D", page=0, revision="r1", alarm_code="12345", text="x"
        )
    with pytest.raises(ValueError, match="alarm_code"):
        AlarmHit(manual_id="828D", page=1, revision="r1", alarm_code="abc", text="x")


# --- 3.4 pipeline: ES→EN pages, out-of-corpus, no stale revision ----------


def test_spanish_query_returns_english_passages_with_pages() -> None:
    # The cross-lingual claim itself rests on BGE-M3 and is proven live by the
    # runtime harness; here we prove the pipeline an operator exercises:
    # Spanish query -> embed -> current-revision search -> English + page.
    model = FakeModel()
    embedder = Embedder(model=model)
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")
    fake.seed(
        "manual_r1_text",
        text_payload(
            page=42,
            child_text="The coolant pressure switch opens at 1.2 bar.",
            parent_text="4.3 Coolant pressure monitoring — English manual page.",
        ),
    )

    query = embedder.encode(["¿cómo reviso la presión del refrigerante?"])
    hits = search(fake, query[0])

    assert len(hits) == 1
    assert hits[0].page == 42
    assert hits[0].child.startswith("The coolant pressure switch")
    assert hits[0].parent.startswith("4.3 Coolant pressure monitoring")
    assert model.calls[0]["texts"] == ["¿cómo reviso la presión del refrigerante?"]


def test_out_of_corpus_query_yields_empty_not_synthesized_content() -> None:
    embedder = Embedder(model=FakeModel())
    fake = FakeQdrant()
    fake.alias("current_text", "manual_r1_text")

    query = embedder.encode(["¿cuál es el precio del repuesto XYZ?"])

    assert search(fake, query[0]) == ()  # refusal feeds on this, nothing else


def test_reindexed_corpus_never_leaks_the_previous_revision() -> None:
    fake = FakeQdrant()
    # r1 was replaced by r2 and the old physical collection is still lying
    # around unaliased; only the alias target may answer.
    fake.alias("current_text", "manual_r2_text")
    fake.seed(
        "manual_r1_text",
        text_payload(revision="r1", page=1, child_text="old corpus"),
    )
    fake.seed(
        "manual_r2_text",
        text_payload(revision="r2", page=9, child_text="new corpus"),
    )

    hits = search(fake, query_vector())

    assert [hit.child for hit in hits] == ["new corpus"]
    assert all(hit.revision == "r2" for hit in hits)
    assert fake.query_calls[0]["collection"] == "manual_r2_text"

    # The alarm side is scoped the same way.
    fake.alias("current_alarms", "manual_r2_alarms")
    fake.seed(
        "manual_r2_alarms",
        alarm_payload(revision="r2", page=9, text="current alarm row"),
    )
    alarm_hits = lookup(fake, "12345")
    assert alarm_hits is not None and all(
        hit.revision == "r2" for hit in alarm_hits
    )


# --- shared Qdrant contract (ingest imports these from store) -------------


def test_alias_and_naming_contract_shared_with_ingestion() -> None:
    # ingest.reindex builds collections through these same helpers: one
    # definition of the contract, no drift between writer and reader.
    assert store.TEXT_ALIAS == "current_text"
    assert store.ALARMS_ALIAS == "current_alarms"
    assert store.text_collection(2) == "manual_r2_text"
    assert store.alarm_collection(2) == "manual_r2_alarms"
    assert revision_tag("manual_r2_text") == "r2"
    assert revision_tag("not-a-collection") is None

    fake = FakeQdrant()
    assert current_target(fake, store.TEXT_ALIAS) is None
    fake.alias("current_text", "manual_r3_text")
    assert current_target(fake, store.TEXT_ALIAS) == "manual_r3_text"


# --- CPU cross-encoder: ordering, degradation, boundary (injected scorer) ---


def _hits(pages: tuple[int, ...]) -> tuple:
    from plant_rag.models import Hit

    return tuple(
        Hit(manual_id="M", page=p, revision="r1", child=f"c{p}", parent=f"p{p}")
        for p in pages
    )


def _scores_in_order(values: list[float]):
    """A scorer that hands back ``values`` in call order, recording pairs."""

    def score(pairs: list[tuple[str, str]]) -> list[float]:
        return values

    return score


def test_rerank_orders_by_score_and_cuts_to_top_k() -> None:
    from plant_rag.retrieve.rerank import rerank

    hits = _hits((10, 20, 30, 40, 50, 60))
    ordered = rerank("pregunta", hits, top_k=3, scorer=_scores_in_order([0.1, 0.9, 0.5, 0.3, 0.8, 0.2]))

    assert [h.page for h in ordered] == [20, 50, 30]


def test_rerank_resolves_ties_in_incoming_order() -> None:
    from plant_rag.retrieve.rerank import rerank

    hits = _hits((10, 20, 30))
    ordered = rerank("pregunta", hits, top_k=3, scorer=_scores_in_order([0.5, 0.5, 0.5]))

    assert [h.page for h in ordered] == [10, 20, 30]  # stable: retrieval order


def test_rerank_with_an_empty_pool_never_loads_the_scorer() -> None:
    from plant_rag.retrieve.rerank import rerank

    def exploding_scorer(pairs):
        raise AssertionError("an empty pool must not load the cross-encoder")

    assert rerank("pregunta", (), top_k=5, scorer=exploding_scorer) == ()


def test_rerank_keeps_retrieval_order_when_scoring_fails() -> None:
    from plant_rag.retrieve.rerank import rerank

    def broken_scorer(pairs):
        raise RuntimeError("scoring failed")

    hits = _hits((10, 20, 30))
    kept = rerank("pregunta", hits, top_k=2, scorer=broken_scorer)

    assert [h.page for h in kept] == [10, 20]  # degraded, never loud


def test_rerank_keeps_retrieval_order_when_the_model_cannot_load(
    monkeypatch,
) -> None:
    from plant_rag.retrieve import rerank as rerank_mod

    def unavailable():
        raise OSError("weights not on disk")

    monkeypatch.setattr(rerank_mod, "_default_scorer", unavailable)
    hits = _hits((10, 20, 30))

    assert [h.page for h in rerank_mod.rerank("pregunta", hits, top_k=2)] == [10, 20]


def test_rerank_rejects_a_score_count_that_does_not_match_the_pool() -> None:
    from plant_rag.retrieve.rerank import rerank

    with pytest.raises(ValueError, match="scores"):
        rerank("pregunta", _hits((10, 20)), top_k=2, scorer=_scores_in_order([0.5]))


def test_rerank_without_a_configured_model_truncates_in_order(monkeypatch) -> None:
    from plant_rag import config
    from plant_rag.retrieve.rerank import rerank

    monkeypatch.setattr(config, "RERANK_MODEL", "")
    hits = _hits((10, 20, 30))

    assert [h.page for h in rerank("pregunta", hits, top_k=2)] == [10, 20]
