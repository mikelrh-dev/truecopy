"""The one answering chain: fast path -> hybrid retrieval -> rerank -> LLM.

Conventions established here (apply to every entrypoint):

* ``answer_question`` IS the generator the queue expects
  (``GenerateFn = (question, remaining_s) -> Answer``), so the CLI
  (``scripts/query.py``), the eval (``scripts/eval.py``), the web layer
  (``web/app.py``), and the integration tests all execute the SAME chain:
  one contract, no drift, no per-layer reimplementation (the three copies
  this replaces were byte-identical logic with byte-identical bugs).
* Order of gates, each one cheaper and stricter than the next:
  1. blank question -> ``ValueError`` (a programming error, not an answer).
  2. exact code-shaped question -> ``fastpath.lookup``: known code produces
     the direct cited alarm answer, unknown code the exact refusal - both
     with ZERO embedding and ZERO LLM traffic (ADR-003/ADR-004).
   3. expand (ES->EN glossary) -> retrieve-and-rank: the question's own pool
      is reranked first; the keyword variant's pool only backfills free slots.
  4. code tokens mentioned in natural language (``... alarma 1192``) are
     unioned in from the alarm collection - the fast path's shape gate
     would otherwise hide known rows from exactly the questions that name
     them (diagnosis: "codes in natural language never use the fast path").
  5. page-diversity: one candidate per (manual, page), alarm rows first,
     so the prompt cites distinct pages instead of five chunks of one.
  6. CPU reranker reorders the pool down to ``config.TOP_K``; on any
     failure it keeps retrieval order (ranking is an optimization, never a
     correctness gate).
  7. ``llm.generate`` applies the evidence gate (empty pool -> exact
     refusal, no network), the citation validator, and the safety warning.
* Every step is a module-level name (``lookup``, ``lookup_code``,
  ``hybrid_search``, ``get_embedder``, ``rerank``, ``llm_generate``), so
  tests monkeypatch the seam they exercise instead of standing up models.
  ``retrieve_pool`` is public for the eval, which re-derives the exact pool
  an answer grounded on when it validates citations.
* Two OPTIONAL per-call seams mirror each other: ``log`` (record the
  answer) and ``cache`` (revision-scoped answer cache). Both default to
  ``None`` — the eval harness passes neither, so harness traffic never
  reads or writes operator state.
"""

from __future__ import annotations

import re
import time

from qdrant_client import QdrantClient

from plant_rag import config
from plant_rag.answer.cache import AnswerCache
from plant_rag.answer.llm import DeclineSignal, Transport, generate as llm_generate
from plant_rag.models import REFUSAL_ES, Answer, Citation, Hit
from plant_rag.monitoring import QueryLog, build_record
from plant_rag.retrieve.embedder import get_embedder
from plant_rag.retrieve.expand import expand_query, keyword_variant
from plant_rag.retrieve.fastpath import AlarmHit, lookup, lookup_code
from plant_rag.retrieve.hybrid import hybrid_search
from plant_rag.retrieve.rerank import rerank
from plant_rag.retrieve.store import current_revision

# 4-6 digits surrounded by word boundaries: the same shape rule as the fast
# path, applied to WHOLE questions so "¿qué significa la alarma 1192?" can
# still reach the alarm table. A 7+ digit run never matches (the boundary
# fails after the 6th digit), so phone numbers and serials stay out.
_CODE_TOKEN_RE = re.compile(r"\b\d{4,6}\b")


def alarm_answer(hits: tuple[AlarmHit, ...]) -> Answer:
    """The direct cited answer for a known alarm code (no LLM involved)."""
    citations = tuple(Citation(manual_id=h.manual_id, page=h.page) for h in hits)
    text = f"Código de alarma {hits[0].alarm_code}: {hits[0].text}"
    return Answer(kind="answer", text_es=text, citations=citations)


def refusal() -> Answer:
    """The exact contract refusal - one construction site, zero network."""
    return Answer(kind="refusal", text_es=REFUSAL_ES, citations=())


def _alarm_hit_to_evidence(row: AlarmHit) -> Hit:
    """Present one alarm row as a prompted passage with page provenance."""
    return Hit(
        manual_id=row.manual_id,
        page=row.page,
        revision=row.revision,
        child=row.text,
        parent=row.text,
    )


def _mentioned_alarm_rows(client: QdrantClient, question: str) -> list[Hit]:
    """Alarm rows for every code-shaped token appearing in the question.

    Unknown codes contribute nothing (an unknown mention is not evidence and
    is NOT a refusal: only a fully code-shaped question owns that gate).
    """
    rows: list[Hit] = []
    for token in dict.fromkeys(_CODE_TOKEN_RE.findall(question)):
        for row in lookup_code(client, token):
            rows.append(_alarm_hit_to_evidence(row))
    return rows


def _page_diverse(pool: list[Hit]) -> list[Hit]:
    """Keep the first candidate per (manual_id, page), preserving order.

    The prompt grounds on page-sized parents and the citation contract is
    page-level: two children of one page would spend a TOP_K slot and a
    reranker slot on the same evidence.
    """
    seen: set[tuple[str, int]] = set()
    diverse: list[Hit] = []
    for hit in pool:
        key = (hit.manual_id, hit.page)
        if key in seen:
            continue
        seen.add(key)
        diverse.append(hit)
    return diverse


def retrieve_pool(
    client: QdrantClient, question: str, query_vector: list[float]
) -> tuple[Hit, ...]:
    """Assemble the candidate pool for ONE retrieval text.

    Hybrid dense+text retrieval, unioned with alarm rows the text names,
    page-diverse and alarm-first. ``question`` here is RETRIEVAL text (the
    ES->EN expanded question or its keyword variant, see :func:`expand_query`):
    the dense branch embeds it and the text branch matches its tokens, while
    the reranker and the LLM further down the chain keep the operator's
    original wording. :func:`retrieve_and_rank` is the public seam the
    scripted eval validates citations against; this is its building block.
    """
    pool = hybrid_search(client, query_vector, question)
    return tuple(_page_diverse(_mentioned_alarm_rows(client, question) + list(pool)))


def retrieve_and_rank(client: QdrantClient, question: str) -> tuple[Hit, ...]:
    """The ranked candidates an answer may cite, built by backfill (not union).

    Multi-query retrieval with BACKFILL semantics: the question's own pool is
    ranked first and its head is final; the keyword variant's pool may only
    fill slots the question could not cover. Ranking the whole union at once
    was tried and rejected (live): variant candidates crowded out pages the
    question had already ranked correctly ("simbolos de luz" lost the
    indicator-lights page to touchscreen-overview pages). A recall aid must
    never displace what the question already got right.

    Public because the scripted eval validates citations against exactly
    this function - the grounding an operator-visible answer depends on.
    """
    expanded = expand_query(question)
    query_vector = get_embedder().encode([expanded])[0]
    hits = rerank(question, retrieve_pool(client, expanded, query_vector), top_k=config.TOP_K)

    variant = keyword_variant(question)
    if variant is None or len(hits) >= config.TOP_K:
        return hits

    taken = {(hit.manual_id, hit.page) for hit in hits}
    variant_expanded = expand_query(variant)
    variant_vector = get_embedder().encode([variant_expanded])[0]
    extra = [
        hit
        for hit in retrieve_pool(client, variant_expanded, variant_vector)
        if (hit.manual_id, hit.page) not in taken
    ]
    backfill = rerank(
        question, tuple(extra), top_k=config.TOP_K - len(hits)
    )
    return hits + backfill


def answer_question(
    question: str,
    remaining_s: float | None = None,
    *,
    client: QdrantClient | None = None,
    transport: Transport | None = None,
    log: QueryLog | None = None,
    cache: AnswerCache | None = None,
) -> Answer:
    """Answer one operator question, recording it when a log is provided.

    Args:
        question: Raw operator query (Spanish).
        remaining_s: Seconds left of the 45s end-to-end budget (the queue
            passes this); ``None`` lets ``llm.generate`` apply the default.
        client: Open Qdrant client (tests inject their double); created
            per call against ``config.QDRANT_URL`` when omitted.
        transport: Injected Ollama transport (tests); the default hits the
            network only when there is evidence to ground on.
        log: Operator-query log to append this answer to. ``None`` (the
            default) logs nothing — the web and CLI entrypoints pass their
            log; the eval passes none, because harness traffic is not
            operator traffic.
        cache: Revision-scoped answer cache: consulted BEFORE the chain and
            stored into AFTER a miss. ``None`` (the default) reads and
            writes nothing — the eval passes none, because harness traffic
            must never see the operator cache.

    Returns:
        A grounded ``Answer``, the exact refusal, the cached twin of
        either, or ``DeadlineExceeded`` from generation - never a partial
        or invented reply.

    Raises:
        ValueError: if ``question`` is blank.
    """
    # Gate 0: blank question -> ValueError (a programming error, not an
    # answer), BEFORE any cache lookup so a blank never keys an entry.
    if not question.strip():
        raise ValueError("question must not be empty")

    started = time.monotonic()
    qdrant = client

    if cache is not None:
        # ONE client for lookup, chain, and re-check. The key needs the
        # current revision (a reindex or model swap invalidates by
        # construction): read once here, re-read only after a miss.
        if qdrant is None:
            qdrant = QdrantClient(url=config.QDRANT_URL, timeout=10)
        revision = current_revision(qdrant)
        cached = cache.lookup(revision, config.OLLAMA_MODEL, question)
        if cached is not None:
            # Hit: the stored answer verbatim (citations, refusal text,
            # baked-in safety warning), logged with its REAL near-zero
            # latency — no `cached` field exists in the log contract.
            if log is not None:
                log.record(
                    build_record(
                        question,
                        cached,
                        time.monotonic() - started,
                        model=config.OLLAMA_MODEL,
                    )
                )
            return cached

    # Miss (or no cache): the full chain. Timeouts and LLM errors
    # propagate here, BEFORE any store, so they can never be cached.
    answer = _answer_question(
        question,
        remaining_s,
        client=qdrant,
        transport=transport,
        decline_signal=(
            None
            if log is None
            else lambda q, hits: log.record_decline_signal(
                q, hits, model=config.OLLAMA_MODEL
            )
        ),
    )

    if cache is not None:
        # Store only if the corpus did not move under the chain's feet
        # (the re-check runs on the miss path only). A store failure must
        # never surface: the answer is already built and is still served.
        try:
            if current_revision(qdrant) == revision:
                cache.store(revision, config.OLLAMA_MODEL, question, answer)
        except Exception:  # noqa: BLE001 - the cache never breaks an answer
            pass

    if log is not None:
        log.record(
            build_record(
                question,
                answer,
                time.monotonic() - started,
                model=config.OLLAMA_MODEL,
            )
        )
    return answer


def _answer_question(
    question: str,
    remaining_s: float | None,
    *,
    client: QdrantClient | None,
    transport: Transport | None,
    decline_signal: DeclineSignal | None = None,
) -> Answer:
    # The blank-question gate lives in ``answer_question`` (before any cache
    # lookup); this private chain assumes a non-blank question.
    qdrant = (
        client if client is not None else QdrantClient(url=config.QDRANT_URL, timeout=10)
    )

    # Gate 1: exact code-shaped question (ADR-003), before any embedding.
    exact = lookup(qdrant, question)
    if exact is None:
        pass  # not code-shaped: semantic path below
    elif not exact:
        return refusal()  # unknown code: exact refusal, no semantic fallback
    else:
        return alarm_answer(exact)

    # Gate 2: evidence. The retrieval text expands the question with the
    # English terms the manual would use (ES->EN glossary): the operator
    # writes "presión neumáticos", the manual says "Tire Pressure". The
    # reranker and the LLM still see the ORIGINAL question below - expansion
    # is a retrieval aid, never a rewrite of what was asked.
    hits = retrieve_and_rank(qdrant, question)

    options: dict = {"remaining_s": remaining_s}
    if transport is not None:
        options["transport"] = transport
    if decline_signal is not None:
        options["decline_signal"] = decline_signal
    return llm_generate(question, hits, **options)
