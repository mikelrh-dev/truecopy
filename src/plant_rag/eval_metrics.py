"""Ranking metrics for the offline eval track (Phase 1, slices 1-2).

Slice 1 asks for the RANK of the hand-verified golden pages (not pass/fail);
slice 2 asks for P@K / R@K over a hand-labeled set. Both are the same math
over the same shape, so they live here, together, as pure functions.

Conventions established here (apply to the whole eval track):

* Pure computation over RECORDED ranks: no Qdrant, no model, no I/O, no
  clock. The harness measures, this module computes - which is exactly what
  makes the numbers reproducible and the regression gate hermetic.
* Relevance is BINARY and PAGE-level, because the citation contract's unit is
  the page: a retrieved page is relevant iff it is in the case's
  ``relevant_pages``. A retrieved list is expected in RANKED order (best
  first), the order the answering chain would cite.
* A relevant page that never appears is rank ``None``, never 0. Treating a
  miss as rank 0 would mean "ranked first" in every average; ``None`` keeps
  MRR honest and is counted in the denominator of every metric that can miss.
* ``k`` defaults to the answering contract's ``TOP_K``: metrics are measured
  over exactly the window an answer is allowed to cite, so a gain that lives
  outside that window is not a gain.
* A NON-refusal case with no relevant pages is a labeling error and raises.
  A refusal control (``expect_refusal=True``) is the opposite label - the
  correct pool is empty - so it is scored as a refusal hit and excluded from
  the ranking means instead of being silently averaged in as a zero.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from plant_rag import config


@dataclass(frozen=True)
class RankedQuery:
    """One measured retrieval: the query, what came back, and what counts.

    ``ranked`` is the retrieved page list in RANKED order (best first, the
    order the answer would cite). ``relevant_pages`` is the hand-labeled
    ground truth for this query; ``expect_refusal`` marks the out-of-corpus
    controls, whose correct outcome is an empty pool.
    """

    query: str
    ranked: tuple[int, ...]
    relevant: frozenset[int]
    expect_refusal: bool = False
    manual_id: str = ""

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not self.expect_refusal and not self.relevant:
            raise ValueError(
                f"{self.query!r} is not a refusal control but labels no "
                "relevant pages: P@K/R@K would be undefined"
            )


@dataclass(frozen=True)
class MetricsReport:
    """The slice-1/slice-2 numbers plus the per-query evidence behind them."""

    k: int
    rows: tuple[RankedQuery, ...]
    ranks: dict[str, int | None] = field(default_factory=dict)

    @property
    def scored(self) -> tuple[RankedQuery, ...]:
        """The ranking rows (refusal controls excluded from the means)."""
        return tuple(row for row in self.rows if not row.expect_refusal)

    @property
    def refusal_ok(self) -> int:
        """Refusal controls whose pool was empty — the correct outcome."""
        return sum(1 for row in self.rows if row.expect_refusal and not row.ranked)

    @property
    def refusal_total(self) -> int:
        """Every refusal control in the set."""
        return sum(1 for row in self.rows if row.expect_refusal)

    @property
    def hit_rate(self) -> float:
        """Hit Rate@K: fraction of scored queries with a relevant page in top K."""
        rows = self.scored
        if not rows:
            return 0.0
        return round(
            sum(1 for row in rows if first_rank(row.ranked[: self.k], row.relevant) is not None)
            / len(rows),
            4,
        )

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank of the FIRST relevant page (misses count 0)."""
        rows = self.scored
        if not rows:
            return 0.0
        total = 0.0
        for row in rows:
            rank = first_rank(row.ranked, row.relevant)
            if rank is not None:
                total += 1.0 / rank
        return round(total / len(rows), 4)

    @property
    def precision(self) -> float:
        """Mean P@K: relevant pages inside the top K, over K."""
        rows = self.scored
        if not rows:
            return 0.0
        return round(sum(precision_at_k(row, self.k) for row in rows) / len(rows), 4)

    @property
    def recall(self) -> float:
        """Mean R@K: labeled pages found inside the top K, over labeled pages."""
        rows = self.scored
        if not rows:
            return 0.0
        return round(sum(recall_at_k(row, self.k) for row in rows) / len(rows), 4)

    def to_dict(self) -> dict[str, object]:
        """The recordable shape (JSON-friendly) the gate compares baselines with."""
        return {
            "k": self.k,
            "queries": len(self.rows),
            "scored": len(self.scored),
            "hit_rate": self.hit_rate,
            "mrr": self.mrr,
            "precision": self.precision,
            "recall": self.recall,
            "refusal_ok": self.refusal_ok,
            "refusal_total": self.refusal_total,
            "ranks": dict(self.ranks),
        }

    def format_es(self) -> str:
        """Operator-readable Spanish block with the per-query ranks."""
        lines = [
            f"Hit Rate@{self.k}: {self.hit_rate}",
            f"MRR: {self.mrr}",
            f"P@{self.k}: {self.precision}",
            f"R@{self.k}: {self.recall}",
            (
                f"Controles fuera de corpus (pool vacío): "
                f"{self.refusal_ok}/{self.refusal_total}"
            ),
        ]
        for row in self.rows:
            if row.expect_refusal:
                outcome = "pool vacío (correcto)" if not row.ranked else (
                    f"pool NO vacío: {list(row.ranked)}"
                )
                lines.append(f"  - {row.query!r} -> {outcome}")
                continue
            rank = self.ranks.get(row.query)
            where = f"rango {rank}" if rank is not None else f"fuera de top-{self.k}"
            lines.append(
                f"  - {row.query!r} -> {where} "
                f"(relevantes {sorted(row.relevant)})"
            )
        return "\n".join(lines)


def first_rank(ranked: tuple[int, ...], relevant: frozenset[int]) -> int | None:
    """1-based rank of the first relevant page, or ``None`` when none appears."""
    for position, page in enumerate(ranked, start=1):
        if page in relevant:
            return position
    return None


def precision_at_k(row: RankedQuery, k: int | None = None) -> float:
    """Relevant pages inside the top ``k``, divided by ``k`` (not by returned)."""
    window = row.ranked[: (config.TOP_K if k is None else k)]
    if not window:
        return 0.0
    divisor = config.TOP_K if k is None else k
    return round(sum(1 for page in window if page in row.relevant) / divisor, 4)


def recall_at_k(row: RankedQuery, k: int | None = None) -> float:
    """Labeled pages found inside the top ``k``, over all labeled pages."""
    if not row.relevant:
        raise ValueError(f"{row.query!r} labels no relevant pages: R@K undefined")
    window = row.ranked[: (config.TOP_K if k is None else k)]
    return round(
        sum(1 for page in window if page in row.relevant) / len(row.relevant), 4
    )


def build_report(rows: tuple[RankedQuery, ...], k: int | None = None) -> MetricsReport:
    """Assemble the report, recording each scored query's rank of first hit."""
    if k is None:
        k = config.TOP_K
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    ranks = {
        row.query: first_rank(row.ranked, row.relevant)
        for row in rows
        if not row.expect_refusal
    }
    return MetricsReport(k=k, rows=tuple(rows), ranks=ranks)


def load_labeled_set(path: str | Path) -> tuple[tuple[RankedQuery, ...], dict]:
    """Load a labeled set: ``(rows, metadata)`` from the checked-in JSON.

    The file carries its own written relevance criteria (slice 2's actual
    deliverable - the numbers are worthless without the rule that produced
    them), which travels back as ``metadata`` so a report can quote it.

    Raises:
        ValueError: on a malformed line, which must fail loudly rather than
            silently shrink the measured set.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: 'cases' must be a non-empty list")
    rows: list[RankedQuery] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"{path}: case {index} is not an object")
        try:
            query = str(case["query"])
            relevant = frozenset(int(page) for page in case.get("relevant_pages", []))
        except KeyError as exc:
            raise ValueError(f"{path}: case {index} is missing {exc}") from exc
        rows.append(
            RankedQuery(
                query=query,
                ranked=(),  # filled by the harness at measurement time
                relevant=relevant,
                expect_refusal=bool(case.get("expect_refusal", False)),
                manual_id=str(case.get("manual_id", data.get("manual_id", ""))),
            )
        )
    metadata = {
        "manual_id": data.get("manual_id", ""),
        "revision": data.get("revision", ""),
        "criteria": data.get("criteria", ""),
        "notes": data.get("notes", ""),
        "status": data.get("status", ""),
        "target_size": data.get("target_size"),
    }
    return tuple(rows), metadata


# ---------------------------------------------------------------------------
# Citation-efficiency metrics (Phase 2a cross-cutting quality signal)
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(
    r"\[\s*fuente\s*:\s*(?P<manual>[^\[\]]+?)\s+p[\u00e1a]gina\s+(?P<page>\d+)\s*\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationReport:
    """Automated citation-quality signal for one answer, for the operator.

    The goal is to flag OVER-CITATION (too many cited pages for the amount of
    fresh text) and DUPLICATE CITATION (the same evidence cited again and
    again), both of which the Q5 live run surfaced.
    """

    query: str
    answer_text: str
    kind: str
    total_citations: int
    unique_pages: int
    max_same_page: int
    chars_per_citation: float
    unique_ratio: float
    over_citation: float

    @property
    def red_flag(self) -> bool:
        """True when the citation pattern is a quality problem."""
        if self.kind in ("refusal", "timeout"):
            return False
        if self.total_citations == 0:
            return False
        return (
            self.total_citations > 8
            or self.unique_ratio < 0.6
            or self.over_citation > 0.3
            or self.chars_per_citation < 60
        )

    @property
    def short(self) -> str:
        """One-line Spanish operator signal."""
        if self.total_citations == 0:
            return "sin citas"
        if self.max_same_page > 3:
            return (
                f"{self.total_citations} citas ({self.max_same_page} idénticas "
                f"de una página, {self.unique_pages} distintas), "
                f"ratio 1:{self.chars_per_citation:.0f} -- cuidado: sobre-citación"
            )
        return (
            f"{self.total_citations} citas, ratio 1:{self.chars_per_citation:.0f} "
            f"caracteres/citas"
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-visible shape for the eval output."""
        return {
            "query": self.query,
            "kind": self.kind,
            "answer_text": self.answer_text,
            "total_citations": self.total_citations,
            "unique_pages": self.unique_pages,
            "max_same_page": self.max_same_page,
            "chars_per_citation": self.chars_per_citation,
            "unique_ratio": self.unique_ratio,
            "over_citation": self.over_citation,
            "red_flag": self.red_flag,
        }


def citation_report(
    query: str, answer_text: str, kind: str, citations: tuple
) -> CitationReport:
    """Build a citation-quality report from the answer and its citations.

    Args:
        query: The operator question (for the eval output).
        answer_text: The answer text the model produced.
        kind: ``answer`` / ``refusal`` / ``timeout``.
        citations: The parsed citations from :func:`validate_citations`.

    Returns:
        A :class:`CitationReport` whose ``red_flag`` is ``True`` when the
        citation pattern is a quality problem (over-citation, low chars/citas,
        heavy duplicate use of one page).

    Raises:
        ValueError: if the answer kind is ``answer`` but has no citations
            (an uncited answer is itself a quality problem, not a citation
            quality issue).
    """
    if kind == "refusal" or kind == "timeout":
        return CitationReport(
            query=query,
            answer_text=answer_text,
            kind=kind,
            total_citations=0,
            unique_pages=0,
            max_same_page=0,
            chars_per_citation=0.0,
            unique_ratio=0.0,
            over_citation=0.0,
        )

    if not citations:
        raise ValueError(f"{query!r}: answer kind {kind!r} but no citations")

    text_len = max(len(answer_text), 1)
    pages = [c.page for c in citations]
    max_same = max(
        (
            sum(1 for p in pages if p == page),
            page,
        )
        for page in set(pages)
    )[0] if pages else 0
    max_same_page = max(
        sum(1 for p in pages if p == page)
        for page in set(pages)
    ) if pages else 0
    unique_ratio = len(set(pages)) / len(pages) if pages else 0.0
    chars_per_citation = text_len / len(pages)
    unique_pages = len(set(pages))
    over_citation = 1.0 - unique_ratio

    return CitationReport(
        query=query,
        answer_text=answer_text,
        kind=kind,
        total_citations=len(pages),
        unique_pages=unique_pages,
        max_same_page=max_same_page,
        chars_per_citation=chars_per_citation,
        unique_ratio=unique_ratio,
        over_citation=over_citation,
    )
