"""Citation validation: every ``[Fuente: … Página N]`` must be retrieved (ADR-004).

Conventions established here:

* LLM output is UNTRUSTED. The validator never repairs or normalizes what the
  model wrote — it either proves every citation marker refers to evidence the
  retriever actually returned, or raises :class:`CitationViolation` and the
  caller refuses (answering spec: "Cited Spanish answers, never invent").
* Validation is three-fold: at least one citation must be present (an
  uncited answer is an uncited claim), every citation-shaped marker must
  parse into a real ``(manual_id, page)`` pair, and every parsed pair must be
  in the retrieved set — a page the model invented fails closed.
* Parsing accepts case and accent variants (``[fuente: … pagina 3]``) so the
  contract checks WHAT was cited, not the model's typography; anything that
  cannot be parsed at all is a violation, never a silently dropped marker.
"""

from __future__ import annotations

import re

from plant_rag.models import Citation, Hit


class CitationViolation(Exception):
    """Raised when the model's citations do not prove retrieved evidence."""


# Loose scanner: every citation-shaped marker in the text, wherever it is.
_MARKER_RE = re.compile(r"\[\s*fuente\s*:", re.IGNORECASE)

# Strict parser: a whole marker with a real page number. ``[áa]`` accepts the
# accent the prompt asks for without rejecting an unaccented copy. The
# ``\s*`` around the brackets accepts the whitespace drift small models
# produce ("[ Fuente: … Página 109 ]"): the contract checks WHAT was cited
# against the retrieved set, never the model's typography (a live 7B refused
# a perfect, grounded answer over exactly one space).
_CITATION_RE = re.compile(
    r"\[\s*fuente\s*:\s*(?P<manual>[^\[\]]+?)\s+p[áa]gina\s+(?P<page>\d+)\s*\]",
    re.IGNORECASE,
)


def validate_citations(text: str, hits: tuple[Hit, ...]) -> tuple[Citation, ...]:
    """Prove every citation in ``text`` refers to one of ``hits``.

    Args:
        text: Untrusted model output.
        hits: The retrieved passages the answer may ground on.

    Returns:
        The parsed citations, in order of appearance.

    Raises:
        CitationViolation: if no citation is present, a citation-shaped
            marker cannot be parsed, or a pair is not among ``hits`` — the
            caller converts this into the exact refusal, never an answer.
    """
    allowed = {(hit.manual_id, hit.page) for hit in hits}
    citations: list[Citation] = []

    for marker in _MARKER_RE.finditer(text):
        close = text.find("]", marker.start())
        segment = text[marker.start() :] if close == -1 else text[marker.start() : close + 1]
        parsed = _CITATION_RE.fullmatch(segment)
        if parsed is None:
            raise CitationViolation(
                f"unparseable citation marker: {segment[:60]!r}"
            )
        citation = Citation(
            manual_id=parsed.group("manual").strip(),
            page=int(parsed.group("page")),
        )
        if (citation.manual_id, citation.page) not in allowed:
            raise CitationViolation(
                f"cited {citation.manual_id} p.{citation.page} was not retrieved"
            )
        citations.append(citation)

    if not citations:
        raise CitationViolation("answer carries no citation at all")
    return tuple(citations)
