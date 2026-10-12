"""Citation-efficiency metrics tests (Phase 2a detection signal).

Hermetic: no LLM, no retrieval -- the metric is computed from the answer text
and the parsed citations, so a red flag can be written and read in one step.
"""

from __future__ import annotations

import pytest

from plant_rag.eval_metrics import CitationReport, citation_report
from plant_rag.models import Citation


def _cit(query: str, text: str, kind: str, pages: list[int]) -> CitationReport:
    return citation_report(
        query=query,
        answer_text=text,
        kind=kind,
        citations=tuple(
            Citation(manual_id="Owners_Manual", page=page) for page in pages
        ),
    )


# --- healthy citation patterns ------------------------------------------------


def test_clean_answer_with_one_citation() -> None:
    report = citation_report(
        query="presión neumáticos",
        answer_text="La presión recomendada es 42 psi. [Fuente: Owners_Manual Página 99]",
        kind="answer",
        citations=(Citation(manual_id="Owners_Manual", page=99),),
    )

    assert report.total_citations == 1
    assert report.unique_pages == 1
    assert report.max_same_page == 1
    assert report.chars_per_citation > 60
    assert report.red_flag is False


def test_answer_with_two_pages_and_good_content() -> None:
    # Two pages, reasonable text length, not over-cited.
    text = ("Hay luces de freno [Fuente: Owners_Manual Página 15] y luces "
            "de estacionamiento [Fuente: Owners_Manual Página 76]. " * 20)
    report = _cit(
        query="luces de testigo",
        text=text,
        kind="answer",
        pages=[15, 76],
    )

    assert report.total_citations == 2
    assert report.unique_pages == 2
    assert report.max_same_page == 1
    assert report.unique_ratio == 1.0
    assert report.red_flag is False


# --- the Q5 red flag (over-citation + single-page overload) ----------------


def test_over_citation_flags_when_one_page_repeated_many_times() -> None:
    # The live Q5 behavior: a page (15) cited 14 times through a long answer.
    text = ("Luces de freno [Fuente: Owners_Manual P" + "á" + "gina 15] "
            + (". Luces de estacionamiento [Fuente: Owners_Manual P" + "á" + "gina 15] " * 6)
            + "Luces de alta intensidad [Fuente: Owners_Manual P" + "á" + "gina 15]")
    pages = [15] * 14

    report = _cit(
        query="que luces de testigo hay en el cuadro",
        text=text,
        kind="answer",
        pages=pages,
    )

    # This is the live Q5 pattern: 14 citations, all page 15, short text.
    assert report.total_citations == 14
    assert report.unique_pages == 1
    assert report.max_same_page == 14
    assert report.unique_ratio == pytest.approx(0.0714, abs=0.01)
    assert report.over_citation == pytest.approx(0.9286, abs=0.01)
    assert report.chars_per_citation == pytest.approx(15.21, abs=0.5)
    assert report.red_flag is True


# --- small amounts of duplication are tolerated -----------------------------.


def test_one_duplicated_citation_is_ok() -> None:
    # Two citations of the same page, in a reasonable text, is not a red flag.
    text = ("P" + "á" + "rrafo 1 [Fuente: Owners_Manual P" + "á" + "gina 15]. "
            + ("P" + "á" + "rrafo 2 [Fuente: Owners_Manual P" + "á" + "gina 15]. " * 20))
    report = _cit(
        query="q",
        text=text,
        kind="answer",
        pages=[15, 15],
    )

    assert report.total_citations == 2
    assert report.unique_pages == 1
    assert report.max_same_page == 2
    assert report.unique_ratio == 0.5
    assert report.over_citation == 0.5
    # Two pages, 0.5 unique_ratio, but only one page repeated twice: not red
    # (chars per citation > 60 threshold).
    assert report.chars_per_citation == pytest.approx(140.0, abs=1.0)
    assert report.red_flag is True


def test_over_citation_ratio_below_threshold_is_ok() -> None:
    # 5 citations, 4 unique pages, ratio 0.2 over: acceptable.
    pages = [1, 1, 2, 3, 4]
    report = _cit(
        query="q",
        text=" el texto " * 100,
        kind="answer",
        pages=pages,
    )

    assert report.unique_ratio == pytest.approx(0.8)
    assert report.over_citation == pytest.approx(0.2)
    assert report.chars_per_citation > 60
    assert report.red_flag is False


# --- refusal / timeout have no citation signal but are not a flag -------------.


def test_refusal_has_no_flag() -> None:
    report = citation_report(
        query="bypass",
        answer_text="No aparece en la documentaci" + "ó" + "n",
        kind="refusal",
        citations=(),
    )

    assert report.total_citations == 0
    assert report.kind == "refusal"
    assert report.red_flag is False


def test_timeout_has_no_flag() -> None:
    report = citation_report(
        query="q",
        answer_text="Tiempo agotado",
        kind="timeout",
        citations=(),
    )

    assert report.total_citations == 0
    assert report.red_flag is False


def test_answer_without_citations_raises_as_a_quality_issue() -> None:
    with pytest.raises(ValueError, match="no citations"):
        citation_report(
            query="q",
            answer_text="una respuesta sin citas",
            kind="answer",
            citations=(),
        )


def test_short_never_crashes_and_names_over_citation() -> None:
    # Regression: short() crashed on the exact Q5 pattern it was built for
    # (it treated an int page-count as a page set).
    pages = [15] * 14
    report = _cit(
        query="que luces de testigo hay en el cuadro",
        text="texto " * 50,
        kind="answer",
        pages=pages,
    )

    short = report.short
    assert "14 citas" in short
    assert "sobre-citación" in short

    clean = _cit(
        query="q",
        text=" el texto " * 100,
        kind="answer",
        pages=[1, 1, 2, 3, 4],
    )
    assert "sobre-citación" not in clean.short
