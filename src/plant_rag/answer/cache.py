"""Revision-scoped operator answer cache (disposable, best-effort, local).

Conventions established here (apply package-wide):

* Cache identity is ``(corpus revision, model, normalize(question))``: a
  reindex or a model swap invalidates every prior entry BY CONSTRUCTION
  (answer-cache spec: "Revision-scoped cache hit"). No TTL and no flush UI
  in v1.
* Storage is disposable operator data: an append-only JSONL file (default
  ``logs/answer-cache.jsonl``, gitignored) plus an in-memory LRU index
  capped at ``config.ANSWER_CACHE_MAX_ENTRIES``. The file is never
  rewritten; eviction happens only in memory. Corrupt records are skipped
  one by one: one bad line never hides the rest, and an unreadable file
  reads as an empty cache. Losing or corrupting the cache degrades latency,
  never answers.
* Every write is best-effort and never raises (the same contract as
  ``monitoring.QueryLog``): the cache is an optimization, never a
  correctness gate.
* Invalidation for answer-relevant config changes (min-score, glossary,
  safety terms, prompt) is BY CONSTRUCTION: the cache key carries a short
  digest of that config, so any change makes every prior entry a miss — no
  manual wipe required (answer-cache spec, assumption A2).
"""

from __future__ import annotations

import hashlib
import json
import threading
import unicodedata
from collections import OrderedDict
from pathlib import Path

from plant_rag import config
from plant_rag.answer.prompt import SAFETY_TERMS, SYSTEM_PROMPT_ES
from plant_rag.models import Answer, Citation
from plant_rag.retrieve.expand import GLOSSARY_ES_EN

# Spanish sentence punctuation an operator types AROUND a question. Trimmed
# from BOTH ENDS only (plus surrounding spaces): mid-question punctuation —
# including quotes and brackets — keeps its meaning untouched, and the same
# question typed two ways still shares one entry.
_EDGE_PUNCTUATION = "¿?¡!.,;:…"


def normalize(question: str) -> str:
    """The canonical cache-key form of an operator question.

    Deterministic and local (no LLM, no network): NFD -> strip combining
    marks (de-accent) -> casefold -> collapse whitespace -> trim Spanish
    punctuation and spaces from both ends. ``"¿Qué es el TIA?"`` and
    ``"que es el tia"`` collapse onto the same key.
    """
    text = unicodedata.normalize("NFD", question)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = " ".join(text.split())
    return text.strip(_EDGE_PUNCTUATION + " ")


def _config_fingerprint() -> str:
    """Short digest of the answer-relevant config (assumption A2).

    Covers everything that can change an answer without changing the corpus
    revision or the model: the relevance floor, retrieval/rerank sizes and
    model, the prompt, the closed safety-term set, and the ES->EN glossary.
    """
    payload = "\n".join(
        (
            f"min_score={config.MIN_SCORE}",
            f"top_k={config.TOP_K}",
            f"rerank_pool={config.RERANK_POOL}",
            f"rerank_model={config.RERANK_MODEL}",
            f"prompt={SYSTEM_PROMPT_ES}",
            f"safety={','.join(SAFETY_TERMS)}",
            f"glossary={'|'.join(f'{k}:{v}' for k, v in GLOSSARY_ES_EN.items())}",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


#:
_CONFIG_FINGERPRINT = _config_fingerprint()

# (revision, model, config fingerprint, normalize(question))
Key = tuple[int | None, str, str, str]


def _key(revision: int | None, model: str, question: str) -> Key:
    return (revision, model, _CONFIG_FINGERPRINT, normalize(question))


def _answer_from_record(data: dict) -> Answer:
    """Rebuild one stored ``Answer``; raises on any malformed field."""
    citations = tuple(
        Citation(manual_id=str(manual_id), page=int(page))
        for manual_id, page in data["citations"]
    )
    return Answer(kind=data["kind"], text_es=data["text_es"], citations=citations)


class AnswerCache:
    """In-memory LRU index over an append-only JSONL answer file."""

    def __init__(self, path: str | Path, *, max_entries: int = 1000) -> None:
        self.path = Path(path)
        self.max_entries = max(1, max_entries)
        self._lock = threading.Lock()
        self._entries: OrderedDict[Key, Answer] = OrderedDict()
        self._load()

    @classmethod
    def from_config(cls) -> AnswerCache | None:
        """The configured cache, or ``None`` when caching is disabled."""
        if not config.ANSWER_CACHE_PATH:
            return None
        return cls(
            config.ANSWER_CACHE_PATH,
            max_entries=config.ANSWER_CACHE_MAX_ENTRIES,
        )

    def lookup(self, revision: int | None, model: str, question: str) -> Answer | None:
        """The stored answer for this key, or ``None`` (a miss)."""
        entry_key = _key(revision, model, question)
        with self._lock:
            answer = self._entries.get(entry_key)
            if answer is None:
                return None
            self._entries.move_to_end(entry_key)  # LRU touch
            return answer

    def store(
        self, revision: int | None, model: str, question: str, answer: Answer
    ) -> None:
        """Append one entry; best-effort — a failure never raises.

        Applied atomically: the file line is appended first and the
        in-memory index only learns the entry when the append succeeded, so
        a failed store leaves no trace at all (memory and file agree).
        """
        record = {
            "revision": revision,
            "model": model,
            "fingerprint": _CONFIG_FINGERPRINT,
            "question": question,
            "kind": answer.kind,
            "text_es": answer.text_es,
            "citations": [[c.manual_id, c.page] for c in answer.citations],
        }
        try:
            line = json.dumps(record, ensure_ascii=False)
        except Exception:  # noqa: BLE001 - unserializable input is a no-op
            return
        entry_key = _key(revision, model, question)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except Exception:  # noqa: BLE001 - the cache never breaks an answer
                return
            self._entries[entry_key] = answer
            self._entries.move_to_end(entry_key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)  # evict the least recent

    def _load(self) -> None:
        """Rebuild the index from the file, skipping anything unreadable."""
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("fingerprint") != _CONFIG_FINGERPRINT:
                            continue  # written under a different config: invalid
                        entry_key = _key(
                            data["revision"], data["model"], data["question"]
                        )
                        answer = _answer_from_record(data)
                    except Exception:  # noqa: BLE001 - skip corrupt records
                        continue
                    self._entries[entry_key] = answer  # a later line wins
                    self._entries.move_to_end(entry_key)
        except Exception:  # noqa: BLE001 - an unreadable file reads as empty
            return
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)  # keep only the newest cap
