"""Shared domain models and contract strings.

Conventions established here (apply package-wide):

* Value objects are frozen dataclasses — retrieved provenance is immutable.
* Operator-facing contract strings are exact Spanish constants; callers must
  never retype them inline (answering spec: "Exact not-found refusal").
* Constructors validate at the boundary and raise ``ValueError`` with an
  actionable message; no silent coercion.
"""

from __future__ import annotations

from dataclasses import dataclass

# Exact reply when the corpus does not cover a query (never LLM-generated).
REFUSAL_ES = "No aparece en la documentación"

# Exact reply when the 45s end-to-end budget is exhausted. The template keeps
# the operator informed without ever fabricating content: {seconds} is filled
# from config.QUERY_DEADLINE_S and {position} from the queue (query-queue
# spec: "Timeout message ... no partial or invented answer is returned").
TIMEOUT_ES = (
    "Se superó el tiempo límite de {seconds} s "
    "(posición {position} en la cola); no se entrega ninguna respuesta parcial"
)


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk with full provenance.

    Every child chunk keeps its manual identifier, page number, parent
    linkage, and revision (manual-ingestion spec: "Batched ingestion with
    provenance" / "Revision metadata from day one") so answers can cite
    Manual + Page and queries can be scoped to the current revision.
    """

    manual_id: str
    page: int
    revision: str
    child: str
    parent: str

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")


@dataclass(frozen=True)
class Citation:
    """One Manual + Page reference — the only claim source an answer may use.

    Built exclusively from retrieved evidence (answering spec: "Cited Spanish
    answers, never invent"): the citation validator maps the LLM's
    ``[Fuente: … Página N]`` markers onto this value object and rejects any
    pair that is not in the retrieved set.
    """

    manual_id: str
    page: int

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")


@dataclass(frozen=True)
class Answer:
    """The operator-facing result of one query (design, "Interfaces/Contracts").

    ``kind`` is the contract surface: ``answer`` (grounded, always cited),
    ``refusal`` (exact ``REFUSAL_ES``, never LLM-generated), or ``timeout``
    (Spanish notice, never partial). The ``__post_init__`` rules enforce the
    "never invent" contract at the boundary: an answer without a citation is
    as invalid as a refusal carrying one.
    """

    kind: str
    text_es: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if self.kind not in ("answer", "refusal", "timeout"):
            raise ValueError(
                f"kind must be 'answer', 'refusal' or 'timeout', got {self.kind!r}"
            )
        if not self.text_es.strip():
            raise ValueError("text_es must not be empty")
        if not isinstance(self.citations, tuple):
            raise ValueError("citations must be a tuple of Citation")
        if any(not isinstance(c, Citation) for c in self.citations):
            raise ValueError("citations must contain only Citation values")
        if self.kind == "answer" and not self.citations:
            raise ValueError("an answer must cite at least Manual + Page")
        if self.kind != "answer" and self.citations:
            raise ValueError(f"a {self.kind} must not carry citations")
