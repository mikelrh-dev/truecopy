"""Cross-lingual ES->EN check: the evidence rule is CONTENT, not page identity.

Hermetic: fake Qdrant, injected retrieval/rerank/generate. The regression this
pins down was live — the check demanded the ONE sampled page number, and at
revision r12 the Tesla manual returns sibling page 277 which carries the very
same boilerplate sentence as sampled page 279, so the check failed while
cross-lingual retrieval was working perfectly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plant_rag import config
from plant_rag.models import Answer, Citation, Hit
from scripts import eval as eval_cli

# >25 words and >=2 sentence terminators, so it passes the check's own
# "is this real prose" gate.
SAMPLE_TEXT = (
    "Inspect the external charging equipment and look for status lights, "
    "displays, or other status indicators on the equipment. Consult the "
    "equipment owner's manual for further troubleshooting instructions. Try "
    "charging the vehicle with different charging equipment or at a "
    "different charging station."
)

# Sibling page: different page number, same boilerplate sentence, different
# surrounding prose. This is the shape that broke the check live.
SIBLING_TEXT = (
    "Try charging the vehicle with different charging equipment or at a "
    "different charging station. For more information on troubleshooting "
    "Mobile Connector or Wall Connector status lights, refer to the "
    "equipment owner's manual supplied with the charging equipment."
)

UNRELATED_TEXT = (
    "The seat belt reminder chime sounds when the driver has not fastened "
    "the belt. Fasten the belt by pulling it across your body. A chime and "
    "an indicator light remind you to buckle up before driving."
)


class FakeClient:
    """Qdrant double: one alias plus the sampled pages' payloads."""

    def __init__(self, pages: list[tuple[int, str]]) -> None:
        self._points = [
            SimpleNamespace(payload={"page": page, "parent_text": text})
            for page, text in pages
        ]

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(
                    alias_name="current_text", collection_name="manual_r12_text"
                )
            ]
        )

    def scroll(self, **kwargs):
        return self._points, None


class FakeEmbedder:
    def encode(self, texts):
        return [[0.0] * config.EMBED_DIMS for _ in texts]


def hit(page: int, parent: str) -> Hit:
    return Hit(
        manual_id="Owners_Manual",
        page=page,
        revision="r12",
        child=parent[:80],
        parent=parent,
    )


@pytest.fixture
def crosslingual(monkeypatch):
    """The check with every heavy seam replaced; returns a configure() helper."""

    def configure(pages: list[tuple[int, str]], hits):
        monkeypatch.setattr(eval_cli, "get_embedder", lambda: FakeEmbedder())
        monkeypatch.setattr(
            eval_cli, "retrieve_pool", lambda client, question, vector: tuple(hits)
        )
        monkeypatch.setattr(
            eval_cli, "rerank", lambda question, pool, top_k=None: tuple(pool)
        )
        monkeypatch.setattr(
            eval_cli,
            "_generate",
            lambda question, remaining_s: Answer(
                kind="answer",
                text_es="La revisión es sencilla. [Fuente: Owners_Manual Página 1]",
                citations=(Citation("Owners_Manual", hits[0].page),),
            ),
        )
        return FakeClient(pages)

    return configure


# --- the evidence rule ------------------------------------------------------


def test_same_page_text_is_evidence() -> None:
    assert eval_cli._shares_evidence(SAMPLE_TEXT, [hit(279, SAMPLE_TEXT)]) is True


def test_sibling_page_sharing_a_distinctive_sentence_is_evidence() -> None:
    # THE FIX: 277 is not 279, but it carries the same boilerplate sentence.
    assert eval_cli._shares_evidence(SAMPLE_TEXT, [hit(277, SIBLING_TEXT)]) is True


def test_unrelated_prose_is_not_evidence() -> None:
    assert eval_cli._shares_evidence(SAMPLE_TEXT, [hit(39, UNRELATED_TEXT)]) is False


def test_blank_or_tiny_text_never_matches() -> None:
    # A page with nothing comparable must not silently count as evidence.
    assert eval_cli._shares_evidence(SAMPLE_TEXT, [hit(1, "")]) is False
    assert eval_cli._shares_evidence("", [hit(1, SAMPLE_TEXT)]) is False
    assert eval_cli._shares_evidence(SAMPLE_TEXT, [hit(1, "See page 12.")]) is False


# --- the check, end to end --------------------------------------------------


def test_check_passes_when_the_sampled_page_comes_back(crosslingual) -> None:
    client = crosslingual([(279, SAMPLE_TEXT)], [hit(279, SAMPLE_TEXT)])

    result = eval_cli.check_crosslingual_es_en(client)

    assert result.passed is True
    assert "retrieved English page 279" in result.detail


def test_check_passes_on_a_sibling_page_and_says_so(crosslingual) -> None:
    # ACCEPTANCE for the fix: the live r12 shape — sample from 279, retrieval
    # returns 277 carrying the same text — must PASS, not fail.
    client = crosslingual([(279, SAMPLE_TEXT)], [hit(277, SIBLING_TEXT)])

    result = eval_cli.check_crosslingual_es_en(client)

    assert result.passed is True
    assert "sibling page" in result.detail


def test_check_still_fails_when_nothing_of_the_sampled_text_returns(
    crosslingual,
) -> None:
    # The rule is not a rubber stamp: unrelated retrieved prose still fails.
    client = crosslingual([(279, SAMPLE_TEXT)], [hit(39, UNRELATED_TEXT)])

    result = eval_cli.check_crosslingual_es_en(client)

    assert result.passed is False
    assert "nor any sibling page" in result.detail
