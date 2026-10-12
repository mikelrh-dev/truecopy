"""Corpus watchdog: read-only health probe for the SERVED corpus (Phase 0).

Conventions established here (apply package-wide):

* Read-only by construction: the probe resolves the ``current_*`` aliases and
  scrolls. It never upserts, deletes, or moves an alias, and the answering
  chain never imports it — the watchdog exists to WARN the operator, never to
  change an answer. A dead corpus must be impossible to miss, not impossible
  to serve.
* The distinction that matters is "dormant by design" vs "dead":
  - An alarm collection with zero rows in the current revision is DORMANT, not
    dead: manuals whose alerts are not tabulated (Tesla, alphanumeric codes)
    legitimately have no alarm table, and ``fastpath.has_alarm_table()``
    already reports ``False`` for them. Never an alert.
  - A text corpus that is missing, outside the ``manual_r{N}_text`` contract,
    empty in the current revision, or holds ONLY zero-norm vectors is DEAD.
    That last case is the incident this phase exists for (live, 2026-10-10):
    a zero-vector encoder indexed cleanly, aliased cleanly, and then answered
    every question with the exact refusal — a silent dead corpus.
* The vector probe is a BOUNDED SAMPLE, not a full scan: one vector carrying
  signal proves the encoder wrote signal, so ``SAMPLE_POINTS`` rows are enough
  and the check stays cheap on a 1024-dim, on-disk corpus. "Zero" is the same
  test the writer uses (``ingest.reindex``): a vector counts as signal iff any
  component exceeds ``_ZERO_VECTOR_EPSILON``.
* The alert is a ``logging`` warning plus the structured :class:`CorpusHealth`
  the caller may render as a UI badge. :meth:`CorpusWatchdog.maybe_check` never
  raises: a watchdog that kills the worker loop it protects is worse than the
  disease, so an unreachable server becomes a DEAD verdict with the transport
  error as its reason.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from qdrant_client import QdrantClient

from plant_rag.retrieve.store import (
    ALARMS_ALIAS,
    TEXT_ALIAS,
    current_target,
    revision_filter,
    revision_tag,
)

# Same contract as ingest.reindex._ZERO_VECTOR_EPSILON: the writer's refusal
# and the watchdog's alert must never disagree about what "zero" means.
_ZERO_VECTOR_EPSILON = 1e-12

# Bounded vector probe (see the module docstring): a sample, never a scan.
SAMPLE_POINTS = 64

# At most one probe per interval; the console worker loop calls ``maybe_check``
# on every turn and this is what keeps it cheap.
DEFAULT_INTERVAL_S = 900.0

OK = "ok"
DEAD = "dead"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorpusHealth:
    """One read-only verdict about the corpus that is serving right now."""

    status: str
    revision: str | None
    text_collection: str | None
    text_points: int
    alarm_collection: str | None
    alarm_points: int
    sampled_vectors: int
    nonzero_vectors: int
    reasons: tuple[str, ...] = ()

    @property
    def alive(self) -> bool:
        """True when the corpus can answer; the only state that stays quiet."""
        return self.status == OK

    @property
    def alarm_table(self) -> bool:
        """True when the current revision HAS alarm rows (dormant is ``False``)."""
        return self.alarm_points > 0

    def message_es(self) -> str | None:
        """The Spanish operator alert, or ``None`` while the corpus is healthy.

        The wording names the consequence the operator will actually see (the
        exact refusal) and the only fix that exists (reindex), because a
        watchdog line that does not say "reindexa" gets read as noise.
        """
        if self.alive:
            return None
        detail = "; ".join(self.reasons) if self.reasons else "causa desconocida"
        return (
            f"Corpus no disponible (revisión {self.revision or 'ninguna'}): "
            f"{detail}. Toda pregunta responderá con la negativa exacta hasta "
            f"reindexar."
        )


def _vector_values(vector: object) -> Iterable[float]:
    """Every component of a stored vector, named or unnamed.

    Qdrant returns either a flat sequence (unnamed vector) or a mapping of
    vector name to sequence (named vectors). Both shapes are accepted so the
    probe keeps working if the collection ever grows a named vector, and an
    unreadable shape simply yields no components — which is "no signal", the
    conservative direction for an alert.
    """
    if vector is None:
        return ()
    if isinstance(vector, dict):
        values: list[float] = []
        for named in vector.values():
            values.extend(float(value) for value in named)
        return values
    try:
        return [float(value) for value in vector]  # type: ignore[union-attr]
    except TypeError:
        return ()


def _has_signal(vector: object) -> bool:
    """Whether a stored vector carries anything (writer rule: any component)."""
    return any(abs(value) > _ZERO_VECTOR_EPSILON for value in _vector_values(vector))


def _scan(
    client: QdrantClient, collection: str, revision: str, *, with_vectors: bool
) -> tuple[object, ...]:
    """One bounded, revision-scoped scroll — the vector probe."""
    points, _ = client.scroll(
        collection_name=collection,
        scroll_filter=revision_filter(revision),
        limit=SAMPLE_POINTS,
        with_payload=False,
        with_vectors=with_vectors,
    )
    return tuple(points)


def _count(client: QdrantClient, collection: str, revision: str) -> int:
    """Exact, revision-scoped point count for one collection.

    ``count`` rather than ``len(sample)``: the probe is bounded at
    ``SAMPLE_POINTS``, so the sample length is a floor ("at least 64"), and a
    floor reported as a count is a wrong number the operator would act on.
    The revision filter keeps a stray point from another revision out of the
    number, the same way every read in the serving path is scoped.
    """
    return int(
        client.count(
            collection_name=collection,
            count_filter=revision_filter(revision),
            exact=True,
        ).count
    )


def check(client: QdrantClient) -> CorpusHealth:
    """Probe the served corpus and return the verdict (pure read, no side effect).

    Args:
        client: Qdrant client (real or test double).

    Returns:
        :class:`CorpusHealth` with ``alive`` True only when the text corpus is
        resolvable, non-empty in its revision, and not uniformly zero-norm.
        Alarm emptiness is reported (``alarm_table``) and never treated as a
        fault — that is the dormant-by-design case.
    """
    reasons: list[str] = []
    text_target = current_target(client, TEXT_ALIAS)
    alarm_target = current_target(client, ALARMS_ALIAS)

    revision: str | None = None
    text_points = 0
    sampled = 0
    nonzero = 0

    if text_target is None:
        reasons.append(
            "no hay alias 'current_text': no se ha indexado ningún manual"
        )
    else:
        revision = revision_tag(text_target)
        if revision is None:
            reasons.append(
                f"el alias 'current_text' apunta a {text_target!r}, fuera del "
                "contrato manual_r{N}_text"
            )
        else:
            text_points = _count(client, text_target, revision)
            sampled_points = _scan(
                client, text_target, revision, with_vectors=True
            )
            sampled = len(sampled_points)
            if sampled == 0:
                reasons.append(
                    f"{text_target} no contiene filas en la revisión {revision}"
                )
            else:
                nonzero = sum(
                    1
                    for point in sampled_points
                    if _has_signal(getattr(point, "vector", None))
                )
                if nonzero == 0:
                    reasons.append(
                        f"los {sampled} vectores muestreados de {text_target} "
                        "tienen norma cero: el corpus está muerto"
                    )

    alarm_points = 0
    if alarm_target is not None:
        alarm_revision = revision_tag(alarm_target)
        if alarm_revision is not None:
            alarm_points = _count(client, alarm_target, alarm_revision)

    return CorpusHealth(
        status=DEAD if reasons else OK,
        revision=revision,
        text_collection=text_target,
        text_points=text_points,
        alarm_collection=alarm_target,
        alarm_points=alarm_points,
        sampled_vectors=sampled,
        nonzero_vectors=nonzero,
        reasons=tuple(reasons),
    )


def alert(health: CorpusHealth) -> None:
    """Emit the operator alert for a DEAD corpus; silent while healthy."""
    message = health.message_es()
    if message is not None:
        logger.warning("%s", message)


class CorpusWatchdog:
    """Interval-throttled, read-only corpus probe (the Phase 0 scheduling hook).

    The console worker loop (or a script) calls :meth:`maybe_check` on every
    turn; the throttle makes that free and the probe runs at most once per
    ``interval_s``. Nothing here is wired into the answering chain: the probe
    reports, the operator acts.
    """

    def __init__(
        self,
        client: QdrantClient,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            client: Qdrant client (real or test double).
            interval_s: Minimum seconds between two probes (``> 0``).
            clock: Injectable time source (``time.monotonic`` in production).

        Raises:
            ValueError: if ``interval_s`` is not positive — a watchdog that
                probes on every call is a hidden per-query cost.
        """
        if interval_s <= 0:
            raise ValueError(f"interval_s must be > 0, got {interval_s}")
        self._client = client
        self._interval_s = float(interval_s)
        self._clock = clock
        self._last_check: float | None = None
        self.last_health: CorpusHealth | None = None

    def check_now(self) -> CorpusHealth:
        """Probe now, unconditionally, and alert on a dead corpus."""
        self._last_check = self._clock()
        health = check(self._client)
        self.last_health = health
        alert(health)
        return health

    def maybe_check(self) -> CorpusHealth | None:
        """Probe iff the interval elapsed; never raises.

        Returns:
            The fresh :class:`CorpusHealth`, or ``None`` when it is not yet due.
            An unreachable server is a DEAD verdict whose reason carries the
            transport error — the loop this protects keeps running either way.
        """
        now = self._clock()
        if (
            self._last_check is not None
            and now - self._last_check < self._interval_s
        ):
            return None
        try:
            return self.check_now()
        except Exception as exc:  # noqa: BLE001 - the watchdog must never crash
            health = CorpusHealth(
                status=DEAD,
                revision=None,
                text_collection=None,
                text_points=0,
                alarm_collection=None,
                alarm_points=0,
                sampled_vectors=0,
                nonzero_vectors=0,
                reasons=(f"la sonda del corpus falló: {exc}",),
            )
            self._last_check = now
            self.last_health = health
            alert(health)
            return health
