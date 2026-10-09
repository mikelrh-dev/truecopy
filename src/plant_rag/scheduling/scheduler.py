"""Query queue: heap ``(aged, safety_rank, seq)`` order and the 45s deadline (ADR-005).

Conventions established here:

* One worker serializes generation (query-queue spec: "FIFO single-worker
  processing"). Dispatch picks the minimum of the heap key
  ``(aged, safety_rank, seq)``: ``aged`` ranks first (0 when the wait has
  crossed ``AGED_AFTER_S``, so an aged query beats a *fresh* safety query —
  anti-starvation), ``safety_rank`` puts the closed-term safety query ahead
  of fresh routine ones, and ``seq`` keeps FIFO inside each class.
* Keys are time-dependent (a query ages while waiting), so the heap is
  re-keyed against the current clock on every dispatch. Queues are LAN-sized,
  so re-heapifying per dispatch is trivially cheap and always correct.
* The deadline is absolute: ``enqueued_at + config.QUERY_DEADLINE_S`` gates
  DISPATCH (an expired entry becomes a Spanish timeout answer without ever
  reaching generation) and GENERATION (the generator receives only the
  remaining budget, and a call that outlives it — or a generator that
  reports :class:`~plant_rag.answer.llm.DeadlineExceeded` — yields the same
  timeout answer). No partial or late answer is ever returned.
* ``submit`` reports the caller's queue position (Spanish helper provided),
  which is also the position quoted back in the timeout message.
* Thread safety: mutations are lock-guarded because the Streamlit web layer
  (Phase 6) submits from concurrent sessions while the worker dispatches;
  generation itself runs outside the lock so operators can keep joining the
  queue while one answer is being produced.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from plant_rag import config
from plant_rag.answer.llm import DeadlineExceeded
from plant_rag.answer.prompt import is_safety_query
from plant_rag.models import TIMEOUT_ES, Answer

# ADR-005: a wait of at least 15s makes a query "aged", which outranks every
# fresh query (including safety) so routine traffic can never starve.
AGED_AFTER_S = 15.0

# The generator contract: (query, remaining seconds of the deadline) -> Answer.
GenerateFn = Callable[[str, float], Answer]


@dataclass
class _Entry:
    """One waiting query with everything the heap key and timeout need."""

    seq: int
    query: str
    safety: bool
    enqueued_at: float
    position: int  # queue position reported at submit (used in timeouts)
    deadline: float = field(init=False)

    def __post_init__(self) -> None:
        self.deadline = self.enqueued_at + config.QUERY_DEADLINE_S

    def sort_key(self, now: float) -> tuple[int, int, int]:
        """Heap key: aged first, then safety, then arrival order (ADR-005)."""
        aged = 0 if now - self.enqueued_at >= AGED_AFTER_S else 1
        safety_rank = 0 if self.safety else 1
        return (aged, safety_rank, self.seq)


def queue_position_es(position: int) -> str:
    """Spanish queue-position feedback for the UI (query-queue spec)."""
    return f"Posición en la cola: {position}"


def timeout_answer(position: int) -> Answer:
    """The Spanish timeout notice — never partial, never citing."""
    return Answer(
        kind="timeout",
        text_es=TIMEOUT_ES.format(seconds=config.QUERY_DEADLINE_S, position=position),
        citations=(),
    )


class Scheduler:
    """FIFO single worker with rule-based safety preemption and aging."""

    def __init__(
        self,
        generate: GenerateFn,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            generate: Receives ``(query, remaining_s)`` and returns an
                ``Answer``; may raise ``DeadlineExceeded`` when it runs out
                of budget. Production wires retrieval + ``answer.llm.generate``
                here; tests inject a fake.
            clock: Injectable time source (``time.monotonic`` in production).
        """
        if not callable(generate):
            raise ValueError("generate must be callable")
        self._generate = generate
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting: list[_Entry] = []
        self._generating: _Entry | None = None
        self._seq = 0

    @property
    def pending(self) -> int:
        """Queries waiting right now (excludes the one being generated)."""
        with self._lock:
            return len(self._waiting)

    def submit(self, query: str) -> int:
        """Enqueue ``query`` and return its Spanish-reported queue position.

        Raises:
            ValueError: if ``query`` is blank.
        """
        if not query.strip():
            raise ValueError("query must not be empty")
        with self._lock:
            self._seq += 1
            # Position = queries still waiting + the one in flight + this one.
            position = len(self._waiting) + (1 if self._generating else 0) + 1
            self._waiting.append(
                _Entry(
                    seq=self._seq,
                    query=query,
                    safety=is_safety_query(query),
                    enqueued_at=self._clock(),
                    position=position,
                )
            )
            return position

    def run_once(self) -> Answer | None:
        """Serve one query (or report one timeout); ``None`` when idle.

        Expired entries are reported first — one per call, oldest expiry
        first — so a backlog can never hide a timeout behind fresh traffic.
        """
        with self._lock:
            now = self._clock()
            expired = [e for e in self._waiting if now >= e.deadline]
            if expired:
                entry = min(expired, key=lambda e: e.seq)
                self._waiting.remove(entry)
                return timeout_answer(entry.position)
            if not self._waiting:
                return None
            entry = self._pop_next(now)
            self._generating = entry
            remaining = entry.deadline - now

        try:
            answer = self._generate(entry.query, remaining)
        except DeadlineExceeded:
            return timeout_answer(entry.position)
        finally:
            with self._lock:
                self._generating = None

        if self._clock() >= entry.deadline:
            # The generator outlived the deadline without reporting it:
            # discard its answer — a late answer is a timeout (ADR-005).
            return timeout_answer(entry.position)
        return answer

    def _pop_next(self, now: float) -> _Entry:
        """Remove and return the highest-priority entry for ``now`` (heap)."""
        heap: list[tuple[tuple[int, int, int], _Entry]] = [
            (entry.sort_key(now), entry) for entry in self._waiting
        ]
        heapq.heapify(heap)
        _, entry = heapq.heappop(heap)
        self._waiting.remove(entry)
        return entry
