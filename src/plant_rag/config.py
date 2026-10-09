"""Runtime configuration for the local-only plant RAG system.

Conventions established here (apply package-wide):

* Every setting has a safe local default and can be overridden through a
  ``PLANT_RAG_*`` environment variable, so operators never edit code.
* Endpoints default to loopback: no documentation ever leaves the machine.
* Invalid numeric overrides fail fast with ``ValueError`` at import time.
"""

from __future__ import annotations

import os

_ENV_PREFIX = "PLANT_RAG_"


def _env_str(name: str, default: str) -> str:
    """Return the ``PLANT_RAG_<name>`` override or ``default``."""
    return os.environ.get(_ENV_PREFIX + name, default)


def _env_int(name: str, default: int) -> int:
    """Return the ``PLANT_RAG_<name>`` integer override or ``default``.

    Raises:
        ValueError: if the override is set but is not an integer.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_ENV_PREFIX}{name} must be an integer, got {raw!r}"
        ) from exc


def _env_float(name: str, default: float) -> float:
    """Return the ``PLANT_RAG_<name>`` float override or ``default``.

    Raises:
        ValueError: if the override is set but is not a number.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_ENV_PREFIX}{name} must be a number, got {raw!r}"
        ) from exc


# --- Service endpoints (local-only defaults) -------------------------------
QDRANT_URL = _env_str("QDRANT_URL", "http://127.0.0.1:6333")
OLLAMA_URL = _env_str("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = _env_str("OLLAMA_MODEL", "qwen2.5:7b-instruct")

# --- Ingestion ------------------------------------------------------------
# 100-page batches keep peak RSS inside the 16GB budget (ADR-002).
BATCH_PAGES = _env_int("BATCH_PAGES", 100)
# BGE-M3 pooled output size (ADR-002 pin): collections are created for this
# dimension before the first batch is encoded, so the reindex pipeline can
# create revisions without encoding anything twice.
EMBED_DIMS = _env_int("EMBED_DIMS", 1024)
# Children returned per semantic query: a small, page-diverse set keeps the
# prompt inside the 8k context (ADR-004) while still covering the answer.
TOP_K = _env_int("TOP_K", 5)
# Candidate pool ranked by retrieval (dense + text, before reranking): the
# reranker reorders these down to TOP_K, so it must be the deeper of the two.
# ``BAAI/bge-reranker-m3`` does not exist on the Hub (401 = missing repo);
# v2-m3 is BAAI's multilingual cross-encoder, which the ES->EN pair scoring
# needs. Set to "" to disable reranking (falls back to retrieval order).
RERANK_MODEL = _env_str("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_POOL = _env_int("RERANK_POOL", 20)
if RERANK_POOL < TOP_K:
    raise ValueError(
        f"{_ENV_PREFIX}RERANK_POOL ({RERANK_POOL}) must be >= TOP_K ({TOP_K})"
    )
# BGE-M3 forward-pass batch on CPU: 32 children x ~1000 chars stay well inside
# the 2.5GB fp32 RAM line from ADR-002 without slowing encoding down.
EMBED_BATCH_SIZE = _env_int("EMBED_BATCH_SIZE", 32)

# --- Answering -------------------------------------------------------------
# Relevance floor for semantic hits (decision deferred from Unit 3, closed
# here with its measurements): BGE-M3 cosine scores measured live against the
# 3-page corpus were 0.571/0.467 for in-corpus ES->EN questions vs
# 0.365/0.314 for out-of-corpus questions (runtime harness, batch 3). 0.40
# sits inside that gap, so a weak match behaves exactly like no match at all:
# store.search drops it, and the empty-result gate refuses with REFUSAL_ES
# WITHOUT calling the LLM (ADR-004). Calibration is provisional — the scripted
# eval (Phase 7) recalibrates on the real manual and may move this value.
MIN_SCORE = _env_float("MIN_SCORE", 0.40)
if not -1.0 <= MIN_SCORE <= 1.0:
    raise ValueError(
        f"{_ENV_PREFIX}MIN_SCORE must be within [-1, 1], got {MIN_SCORE}"
    )

# End-to-end answer budget in seconds (answering + query-queue specs, A1):
# queue wait and generation share this ONE deadline. The scheduler gates
# dispatch on it and hands the remainder to generation (ADR-005); the LLM
# transport falls back to it when no deadline is passed.
QUERY_DEADLINE_S = _env_int("QUERY_DEADLINE_S", 45)
if QUERY_DEADLINE_S < 1:
    raise ValueError(
        f"{_ENV_PREFIX}QUERY_DEADLINE_S must be >= 1, got {QUERY_DEADLINE_S}"
    )

# --- Monitoring -------------------------------------------------------------
# Append-only JSONL log of operator questions (one record per answer). The
# web layer and the CLI wire it; the eval does not (its traffic is not
# operator traffic). Set to "" to disable query logging entirely.
QUERY_LOG_PATH = _env_str("QUERY_LOG_PATH", "logs/queries.jsonl")
