"""Cross-lingual query expansion tests (retrieval-only ES->EN glossary)."""

from __future__ import annotations

from plant_rag.retrieve.expand import expand_query, expansion_terms, keyword_variant


def test_expansion_appends_the_english_terms_the_manual_uses() -> None:
    expanded = expand_query("presion neumaticos")

    assert expanded.startswith("presion neumaticos")  # original first, always
    # "neumaticos" matches the PLURAL glossary entry (word boundaries).
    terms = expansion_terms("presion neumaticos")
    assert "tires" in terms and "pressure" in terms
    assert "tires" in expanded
    # The singular question expands to the singular terms.
    singular = expansion_terms("presion del neumatico")
    assert "tire" in singular and "pressure" in singular


def test_expansion_is_accent_and_case_insensitive() -> None:
    assert expansion_terms("PRESIÓN Neumáticos") == expansion_terms(
        "presion neumaticos"
    )


def test_question_without_glossary_hits_is_returned_unchanged() -> None:
    question = "¿quién es el presidente de francia?"

    assert expand_query(question) == question
    assert expansion_terms(question) == ()


def test_expansion_is_additive_and_capped() -> None:
    # A kitchen-sink question must not drown its own embedding.
    verbose = (
        "presion aceite filtro freno bateria carga puerta cinturon volante "
        "luces intermitente limpiaparabrisas capo maletero remolque alarma "
        "sensor temperatura refrigerante liquido"
    )
    terms = expansion_terms(verbose)

    assert len(terms) <= 8  # _MAX_EXPANSION_TERMS
    assert len(terms) >= 1  # but it still expands


def test_word_boundaries_keep_unrelated_words_out() -> None:
    # "presionista" contains "presion" but is not the word "presión".
    assert "pressure" not in expansion_terms("presionista famoso")


def test_keyword_variant_strips_question_scaffolding() -> None:
    variant = keyword_variant("¿Cómo hago para resetear el magazine?")

    assert variant is not None
    assert "magazine" in variant
    assert "como" not in variant.split()
    assert "para" not in variant.split()


def test_keyword_variant_is_none_when_the_question_is_already_keywords() -> None:
    # A keyword question must not trigger a second (identical) query.
    assert keyword_variant("presion neumaticos") is None
    assert keyword_variant("10730") is None


def test_keyword_variant_is_none_when_nothing_contentful_remains() -> None:
    assert keyword_variant("¿cómo hago?") is None
    assert keyword_variant("¿qué es?") is None
