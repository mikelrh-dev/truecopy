"""Ingestion tests (Phase 2, Work Unit 2).

Hermetic by contract: no model downloads, no running Qdrant, no real corpus.
PDFs are generated on the fly with the pinned PyMuPDF, and Qdrant is replaced
by an in-memory fake that mirrors the client calls reindex depends on
(collections, payload index, upserts, alias swap). The real Qdrant path is
exercised by the runtime harness reported separately from this suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import fitz
import pytest

from plant_rag import config
from plant_rag.ingest.alarms import (
    AlarmRow,
    alarms_from_lines,
    alarms_from_tables,
    extract_alarms,
)
from plant_rag.ingest.chunker import CHILD_MAX_CHARS, chunk_page
from plant_rag.ingest.loader import Page, iter_batches
from plant_rag.ingest.reindex import run_reindex


def make_pdf(path, pages: list[str]):
    """Write a minimal PDF with one text block per page and return its path."""
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


# --- 2.1 loader: batches, provenance, RSS bound ---------------------------


def test_iter_batches_splits_document_into_bounded_batches(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "m.pdf", [f"Page {i} body" for i in range(1, 8)])
    batches = list(iter_batches(pdf, batch_pages=3))

    assert [len(b) for b in batches] == [3, 3, 1]
    # Sequential, lossless coverage: every page exactly once, in order.
    numbers = [page.number for batch in batches for page in batch]
    assert numbers == list(range(1, 8))
    texts = [page.text for batch in batches for page in batch]
    assert texts[0].strip() == "Page 1 body"
    assert texts[-1].strip() == "Page 7 body"


def test_iter_batches_default_uses_config_100_page_bound(tmp_path) -> None:
    # ADR-002: 100-page batches keep peak RSS inside the 16GB budget.
    pdf = make_pdf(tmp_path / "big.pdf", [f"P{i}" for i in range(1, 106)])
    sizes = [len(batch) for batch in iter_batches(pdf)]

    assert config.BATCH_PAGES == 100
    assert sizes == [100, 5]
    assert max(sizes) <= config.BATCH_PAGES


def test_iter_batches_is_lazy_so_only_one_batch_resides_in_memory(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "m.pdf", [f"Page {i}" for i in range(1, 11)])
    stream = iter_batches(pdf, batch_pages=4)

    # A generator: nothing is extracted until consumed, and consumption is
    # batch-at-a-time — the structural basis of the memory bound.
    first = next(stream)
    assert len(first) == 4
    second = next(stream)
    assert len(second) == 4
    assert [p.number for p in first] == [1, 2, 3, 4]
    assert [p.number for p in second] == [5, 6, 7, 8]


def test_iter_batches_rejects_invalid_batch_size(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "m.pdf", ["text"])
    with pytest.raises(ValueError, match="batch_pages"):
        list(iter_batches(pdf, batch_pages=0))


def test_iter_batches_missing_file_fails_actionably(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="manual PDF not found"):
        list(iter_batches(tmp_path / "absent.pdf"))


def test_iter_batches_maps_outline_titles_onto_pages(tmp_path) -> None:
    # Section titles from the PDF outline ride on every page (retrieval
    # context): the deepest entry covering a page wins.
    path = tmp_path / "toc.pdf"
    doc = fitz.open()
    for _ in range(4):
        doc.new_page()
    doc.set_toc(
        [
            [1, "Winter Driving", 1],
            [2, "Winter Tires", 2],
            [1, "Charging", 4],
        ]
    )
    doc.save(str(path))
    doc.close()

    sections = {
        page.number: page.section
        for batch in iter_batches(path, batch_pages=10)
        for page in batch
    }

    assert sections[1] == "Winter Driving"
    assert sections[2] == "Winter Tires"  # deepest entry on the page
    assert sections[3] == "Winter Tires"  # section owns pages until the next
    assert sections[4] == "Charging"


def test_iter_batches_pages_without_outline_have_no_section(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "m.pdf", ["body one", "body two"])
    pages = [page for batch in iter_batches(pdf, batch_pages=10) for page in batch]

    assert [page.section for page in pages] == [None, None]


def test_page_rejects_invalid_provenance() -> None:
    with pytest.raises(ValueError, match="page number"):
        Page(number=0, text="x")


# --- 2.2 chunker: parent-child chunks with provenance ---------------------


def test_chunk_page_links_every_child_to_manual_page_parent_and_revision() -> None:
    page = Page(number=12, text="Coolant pressure switch behaviour under load. " * 5)
    parent, children = chunk_page(page, manual_id="828D", revision="r1")

    assert parent is not None
    assert (parent.manual_id, parent.page, parent.revision) == ("828D", 12, "r1")
    assert children, "a non-empty page must produce at least one child"
    for child in children:
        assert (child.manual_id, child.page, child.revision) == ("828D", 12, "r1")
        assert child.parent_id == parent.parent_id
        assert child.child_id.startswith(parent.parent_id)
    assert [c.child_id for c in children] == [
        f"{parent.parent_id}:c{i}" for i in range(len(children))
    ]


def test_chunk_page_section_prefix_rides_children_but_never_parents() -> None:
    # Retrieval context for children; verbatim evidence for the answer.
    page = Page(
        number=7,
        text="Use winter tires to increase traction in snowy conditions. " * 5,
        section="Winter Tires",
    )
    parent, children = chunk_page(page, manual_id="Owners_Manual", revision="r6")

    assert parent is not None
    assert "[Winter Tires]" not in parent.text  # the LLM evidence stays verbatim
    for child in children:
        assert child.text.startswith("[Winter Tires] ")
        assert child.text.endswith(children[-1].text[-40:])


def test_chunk_page_without_section_adds_no_prefix() -> None:
    text = "Plain body text without an outline entry. " * 3
    page = Page(number=7, text=text)
    parent, children = chunk_page(page, manual_id="Owners_Manual", revision="r6")

    assert parent is not None
    assert len(children) == 1
    assert children[0].text == text.strip()  # window verbatim, nothing added


def test_chunk_page_windows_cover_long_pages_within_bounds() -> None:
    text = " ".join(f"token{i}" for i in range(600))  # well past one window
    page = Page(number=1, text=text)
    parent, children = chunk_page(page, manual_id="828D", revision="r1")

    assert parent is not None and len(children) >= 2
    assert all(len(c.text) <= CHILD_MAX_CHARS for c in children)
    # Both ends of the page survive the split, so no content is dropped.
    assert children[0].text.startswith(text[:50])
    assert children[-1].text.endswith(text[-50:])


def test_chunk_page_skips_blank_pages() -> None:
    parent, children = chunk_page(Page(number=3, text="  \n "), manual_id="828D", revision="r1")
    assert parent is None and children == []


def test_chunk_page_rejects_invalid_provenance() -> None:
    page = Page(number=1, text="body")
    with pytest.raises(ValueError, match="manual_id"):
        chunk_page(page, manual_id=" ", revision="r1")
    with pytest.raises(ValueError, match="revision"):
        chunk_page(page, manual_id="828D", revision="")


# --- 2.3 alarms: find_tables() first cell + line-regex fallback -----------


def test_alarms_from_tables_take_rows_whose_first_cell_is_a_code() -> None:
    grids = [
        [
            ["1000", "Spindle overtemp"],
            ["N/A", "header row, not an alarm"],
            ["1234567", "seven digits is not a code"],
            [" 2045 ", "padded code cell"],
            [None, "empty first cell"],
            ["999999", "six digits is valid"],
        ]
    ]
    rows = alarms_from_tables(grids, manual_id="828D", page=4, revision="r1")

    assert [(r.alarm_code, r.text) for r in rows] == [
        ("1000", "Spindle overtemp"),
        ("2045", "padded code cell"),
        ("999999", "six digits is valid"),
    ]
    assert all((r.manual_id, r.page, r.revision) == ("828D", 4, "r1") for r in rows)


def test_alarms_line_fallback_matches_only_code_shaped_lines() -> None:
    text = (
        "1001  Coolant pressure low\n"
        "Manual 828D revision notes\n"
        "1234567 not an alarm code\n"
        "  2150   Feed hold active\n"
        "2045\n"
    )
    rows = alarms_from_lines(text, manual_id="828D", page=9, revision="r1")

    assert [(r.alarm_code, r.text) for r in rows] == [
        ("1001", "Coolant pressure low"),
        ("2150", "Feed hold active"),
    ]


def test_extract_alarms_prefers_tables_over_line_matches(tmp_path) -> None:
    # A ruled table (per-row horizontal + vertical lines) plus a body line
    # that would also match the fallback: table rows must win exclusively.
    doc = fitz.open()
    page = doc.new_page()
    x0, x1 = 72, 320
    ys = [72, 96, 120, 144]
    mid = (x0 + x1) // 2
    for y in ys:
        page.draw_line((x0, y), (x1, y))
    page.draw_line((x0, ys[0]), (x0, ys[-1]))
    page.draw_line((x1, ys[0]), (x1, ys[-1]))
    page.draw_line((mid, ys[0]), (mid, ys[-1]))
    for i, (code, desc) in enumerate(
        [("1000", "Spindle overtemp"), ("2045", "Coolant pressure low")]
    ):
        page.insert_text((x0 + 4, ys[i] + 15), code, fontsize=10)
        page.insert_text((mid + 4, ys[i] + 15), desc, fontsize=10)
    page.insert_text((72, 164), "3003 Body line alarm also present", fontsize=10)
    pdf = tmp_path / "alarms.pdf"
    doc.save(str(pdf))
    doc.close()

    (batch,) = list(iter_batches(pdf))
    rows = extract_alarms(batch[0], manual_id="828D", revision="r1")

    assert [(r.alarm_code, r.text) for r in rows] == [
        ("1000", "Spindle overtemp"),
        ("2045", "Coolant pressure low"),
    ]
    assert rows[0].page == 1


def test_extract_alarms_falls_back_to_lines_without_tables() -> None:
    page = Page(number=7, text="intro line\n4040  Axis overtravel released\n")
    rows = extract_alarms(page, manual_id="828D", revision="r1")

    assert [(r.alarm_code, r.page) for r in rows] == [("4040", 7)]


def test_alarm_row_rejects_invalid_code() -> None:
    with pytest.raises(ValueError, match="alarm_code"):
        AlarmRow(
            manual_id="828D", page=1, revision="r1", alarm_code="12a4", text="x"
        )


# --- 2.4 reindex: blue/green build, validate, atomic swap -----------------


class FakeQdrant:
    """In-memory stand-in for exactly the Qdrant calls ``run_reindex`` uses.

    Alias updates are applied atomically from a copy (mirroring one Qdrant
    request), and every call is recorded so tests can assert swap atomicity.
    """

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict]] = {}
        self.collection_configs: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.alias_calls: list[list] = []

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.collections]
        )

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=alias, collection_name=target)
                for alias, target in self.aliases.items()
            ]
        )

    def create_collection(self, name, vectors_config=None, **kwargs):
        if name in self.collections:
            raise RuntimeError(f"collection {name} already exists")
        self.collections[name] = {}
        self.collection_configs[name] = {"vectors_config": vectors_config, **kwargs}
        return True

    def create_payload_index(self, *args, **kwargs):
        return True

    def upsert(self, name, points, wait=True):
        bucket = self.collections[name]  # KeyError mirrors Qdrant's 404
        for point in points:
            bucket[str(point.id)] = {"vector": point.vector, "payload": point.payload}
        return True

    def get_collection(self, name):
        return SimpleNamespace(
            points_count=len(self.collections[name]), status="green"
        )

    def update_collection_aliases(self, change_aliases_operations, timeout=None):
        pending = dict(self.aliases)
        for operation in change_aliases_operations:
            create = getattr(operation, "create_alias", None)
            delete = getattr(operation, "delete_alias", None)
            if create is not None:
                pending[create.alias_name] = create.collection_name
            if delete is not None:
                pending.pop(delete.alias_name, None)
        self.alias_calls.append(list(change_aliases_operations))
        self.aliases = pending
        return True

    def delete_collection(self, name):
        if name not in self.collections:
            raise RuntimeError(f"collection {name} does not exist")
        del self.collections[name]
        return True


def fake_encode(texts: list[str]) -> list[list[float]]:
    """Deterministic embedder stand-in: no model, stable dims."""
    return [[float(len(text) % 97)] * config.EMBED_DIMS for text in texts]


class FlakyEncoder:
    """Raises once it has successfully encoded ``succeed_calls`` batches."""

    def __init__(self, succeed_calls: int) -> None:
        self.succeed_calls = succeed_calls
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls > self.succeed_calls:
            raise RuntimeError("embedder died mid-reindex")
        return fake_encode(texts)


def revisions_through(fake: FakeQdrant, alias: str) -> set[str]:
    """Revisions a query would see by resolving one current_* alias."""
    target = fake.aliases.get(alias)
    if target is None:
        return set()
    return {
        point["payload"]["revision"] for point in fake.collections[target].values()
    }


ALARM_PAGES = [
    "1000  Spindle overtemp warning\nSafety circuit notes",
    "2045  Coolant pressure low\nOperator instructions",
    "3003  Axis overtravel released",
]


def test_first_ingestion_builds_revision_and_swaps_both_aliases_once(
    tmp_path,
) -> None:
    pdf = make_pdf(tmp_path / "v1.pdf", ALARM_PAGES)
    fake = FakeQdrant()

    result = run_reindex(pdf, client=fake, encode=fake_encode, manual_id="828D")

    assert result.revision == "r1" and result.previous_revision is None
    assert result.text_points >= 1 and result.alarm_points == 3
    assert fake.aliases == {
        "current_text": "manual_r1_text",
        "current_alarms": "manual_r1_alarms",
    }
    # First swap: both creates in exactly one call, no deletes needed.
    assert len(fake.alias_calls) == 1 and len(fake.alias_calls[0]) == 2
    # Alarms are payload-only — exact MatchValue never touches an embedding.
    assert fake.collection_configs["manual_r1_alarms"]["vectors_config"] == {}
    assert all(
        point["vector"] == {}
        for point in fake.collections["manual_r1_alarms"].values()
    )
    # Every child payload keeps full provenance for Hit construction.
    for point in fake.collections["manual_r1_text"].values():
        payload = point["payload"]
        assert payload["manual_id"] == "828D"
        assert payload["revision"] == "r1"
        assert payload["page"] >= 1
        assert payload["child_id"].startswith(payload["parent_id"])
        assert payload["parent_text"]


def test_replacement_manual_is_the_only_revision_after_swap(tmp_path) -> None:
    first_pdf = make_pdf(tmp_path / "v1.pdf", ALARM_PAGES)
    second_pdf = make_pdf(tmp_path / "v2.pdf", ["2045  Coolant circuit redesign"])
    fake = FakeQdrant()
    run_reindex(first_pdf, client=fake, encode=fake_encode, manual_id="828D")

    result = run_reindex(second_pdf, client=fake, encode=fake_encode, manual_id="828D")

    assert (result.revision, result.previous_revision) == ("r2", "r1")
    assert fake.aliases == {
        "current_text": "manual_r2_text",
        "current_alarms": "manual_r2_alarms",
    }
    # Replacement scenario: queries after reindex see ONLY the new revision.
    assert revisions_through(fake, "current_text") == {"r2"}
    assert revisions_through(fake, "current_alarms") == {"r2"}
    # Prior chunks are gone — nothing stale remains to retrieve.
    assert "manual_r1_text" not in fake.collections
    assert "manual_r1_alarms" not in fake.collections
    # Both aliases moved in ONE call: 2 deletes + 2 creates, atomic.
    assert len(fake.alias_calls) == 2
    assert len(fake.alias_calls[1]) == 4


def test_interrupted_reindex_never_mixes_revisions(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "v.pdf", ALARM_PAGES)
    fake = FakeQdrant()
    run_reindex(pdf, client=fake, encode=fake_encode, manual_id="828D")
    points_before = len(fake.collections["manual_r1_text"])

    # Fails partway: batch 1 of r2 is already upserted when the encoder dies.
    with pytest.raises(RuntimeError, match="embedder died"):
        run_reindex(
            pdf,
            client=fake,
            encode=FlakyEncoder(succeed_calls=1),
            manual_id="828D",
            batch_pages=1,
        )

    # Aliases never moved, so the query path still resolves r1 exclusively.
    assert fake.aliases["current_text"] == "manual_r1_text"
    assert revisions_through(fake, "current_text") == {"r1"}
    assert revisions_through(fake, "current_alarms") == {"r1"}
    assert len(fake.alias_calls) == 1  # no swap was ever attempted
    # The half-built revision is removed, not left lying around unaliased,
    # and the surviving revision kept every point it had before the failure.
    assert "manual_r2_text" not in fake.collections
    assert "manual_r2_alarms" not in fake.collections
    assert len(fake.collections["manual_r1_text"]) == points_before


def test_interrupted_first_ingestion_leaves_no_aliases(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "v.pdf", ALARM_PAGES)
    fake = FakeQdrant()

    with pytest.raises(RuntimeError, match="embedder died"):
        run_reindex(
            pdf,
            client=fake,
            encode=FlakyEncoder(succeed_calls=0),
            manual_id="828D",
            batch_pages=1,
        )

    assert fake.aliases == {}
    assert fake.alias_calls == []
    assert fake.collections == {}  # partial build fully cleaned up


def test_empty_corpus_never_swaps(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "blank.pdf", ["", "", ""])
    fake = FakeQdrant()

    with pytest.raises(ValueError, match="no indexable text"):
        run_reindex(pdf, client=fake, encode=fake_encode, manual_id="828D")

    assert fake.aliases == {} and fake.alias_calls == []
    assert fake.collections == {}


def test_encode_contract_violations_are_rejected_before_swap(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "v.pdf", ALARM_PAGES)
    fake = FakeQdrant()

    def wrong_dims(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]

    with pytest.raises(ValueError, match="dims"):
        run_reindex(pdf, client=fake, encode=wrong_dims, manual_id="828D")

    assert fake.aliases == {} and fake.alias_calls == []
    assert fake.collections == {}


def test_reindex_rejects_blank_manual_id(tmp_path) -> None:
    pdf = make_pdf(tmp_path / "v.pdf", ALARM_PAGES)
    fake = FakeQdrant()
    with pytest.raises(ValueError, match="manual_id"):
        run_reindex(pdf, client=fake, encode=fake_encode, manual_id=" ")


# --- Phase 4: read-only calibration advisory on the ingest CLI -------------


def _seed_calibration_record(
    cal_path,
    *,
    manual_id: str = "828D",
    revision: str = "r3",
    recommended: float | None = 0.4,
    current: float = 0.4,
    age_days: int = 1,
) -> None:
    from plant_rag.calibration import append_record, build_record

    recorded_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    append_record(
        cal_path,
        build_record(
            manual_id,
            revision,
            current_min_score=current,
            in_corpus_hits=5,
            in_total=5,
            out_of_corpus_hits=0,
            out_total=5,
            recommended_min_score=recommended,
            golden_pass=8,
            golden_total=8,
            golden_set_present=True,
            sweep=[{"threshold": 0.4, "in_hits": 5, "out_hits": 0}],
            recorded_at=recorded_at,
        ),
    )


def _patch_ingest_harness(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    cal_path: str,
    *,
    result_revision: str = "r3",
) -> list[dict]:
    """Hermetic ingest CLI: fake client/embedder, monkeypatched reindex.

    Returns the list of reindex call records (manual_id etc.) so tests can
    prove the advisory never replaced or re-triggered ingestion.
    """
    import scripts.ingest as ingest_cli
    from plant_rag import config

    monkeypatch.setattr(config, "CALIBRATION_PATH", cal_path)
    monkeypatch.setattr(
        config, "ANSWER_CACHE_PATH", str(tmp_path / "answer-cache.jsonl")
    )

    class _FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr(ingest_cli, "QdrantClient", _FakeClient)

    class _FakeEmbedder:
        def encode(self, texts: list[str]) -> list[list[float]]:
            return [[0.0] * 8 for _ in texts]

    monkeypatch.setattr(ingest_cli, "get_embedder", lambda: _FakeEmbedder())

    calls: list[dict] = []

    def _fake_reindex(pdf_path, *, client, encode, manual_id, batch_pages):
        calls.append({"manual_id": manual_id, "client": client})
        return SimpleNamespace(
            revision=result_revision,
            text_points=10,
            alarm_points=3,
            previous_revision="r2",
            stale_collections=[],
        )

    monkeypatch.setattr(ingest_cli, "run_reindex", _fake_reindex)
    return calls


def test_ingest_prints_fresh_advisory_after_the_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.ingest as ingest_cli

    cal_path = tmp_path / "calibration.jsonl"
    _seed_calibration_record(cal_path)
    _patch_ingest_harness(monkeypatch, tmp_path, str(cal_path))
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    out = capsys.readouterr().out
    assert "OK — revisión r3 en servicio:" in out  # the summary comes first
    assert "Sugerencia de calibración [fresh]" in out
    assert "separates this labeled set" in out
    assert "(revisión r3)" in out
    assert "borre" not in out  # floors match: no wipe instruction


def test_ingest_no_record_prints_the_no_record_notice(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.ingest as ingest_cli

    cal_path = tmp_path / "calibration.jsonl"  # exists, but no records
    _patch_ingest_harness(monkeypatch, tmp_path, str(cal_path))
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    out = capsys.readouterr().out
    assert "No hay registro de calibración para '828D'" in out
    assert "Sugerencia de calibración" not in out


def test_ingest_stale_record_is_downgraded_not_hidden(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.ingest as ingest_cli

    cal_path = tmp_path / "calibration.jsonl"
    _seed_calibration_record(cal_path, age_days=100)  # >90d, same revision
    _patch_ingest_harness(monkeypatch, tmp_path, str(cal_path))
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    out = capsys.readouterr().out
    assert "Aviso [soft-stale]" in out
    assert "separates this labeled set" in out  # downgraded, never hidden


def test_ingest_hard_stale_when_the_stored_revision_differs(
    tmp_path, monkeypatch, capsys
) -> None:
    """The spec GIVEN end-to-end: a record stored under an older revision.

    The seed below writes revision ``r2`` while the reindex lands ``r3``, so
    the advisory must label the record hard-stale (revision mismatch) — not
    the age-based soft-stale path — while still showing its content.
    """
    import scripts.ingest as ingest_cli

    cal_path = tmp_path / "calibration.jsonl"
    _seed_calibration_record(cal_path, revision="r2")
    _patch_ingest_harness(monkeypatch, tmp_path, str(cal_path))
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    out = capsys.readouterr().out
    assert "Aviso [hard-stale]" in out
    assert "separates this labeled set" in out  # downgraded, never hidden
    assert "golden 8/8" in out  # counts and date travel with the stale line


def test_ingest_with_empty_path_prints_nothing_extra(
    tmp_path, monkeypatch, capsys
) -> None:
    import scripts.ingest as ingest_cli

    _patch_ingest_harness(monkeypatch, tmp_path, "")
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    out = capsys.readouterr().out
    assert "OK — revisión r3 en servicio:" in out
    assert "Sugerencia" not in out
    assert "Aviso" not in out
    assert "No hay registro" not in out


def test_ingest_advisory_never_mutates_config_or_the_store(
    tmp_path, monkeypatch
) -> None:
    """Read-only contract: bytes, config, and cache all survive untouched."""
    import scripts.ingest as ingest_cli
    from plant_rag import config

    cal_path = tmp_path / "calibration.jsonl"
    _seed_calibration_record(cal_path)
    calls = _patch_ingest_harness(monkeypatch, tmp_path, str(cal_path))
    pdf = make_pdf(tmp_path / "v.pdf", ["body"])
    before = cal_path.read_bytes()
    min_score_before = config.MIN_SCORE

    assert ingest_cli.main([str(pdf), "--manual-id", "828D"]) == 0

    assert cal_path.read_bytes() == before  # the store is append-never-read-write
    assert config.MIN_SCORE == min_score_before
    assert not (tmp_path / "answer-cache.jsonl").exists()
    assert calls == [{"manual_id": "828D", "client": calls[0]["client"]}]
    assert len(calls) == 1  # exactly one reindex: the advisory adds none
