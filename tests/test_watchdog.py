"""Corpus watchdog tests (Phase 0).

Hermetic by contract: an in-memory Qdrant double and ``caplog`` — no server,
no vectors, no model. The acceptance pair from the plan's Phase 0 is pinned
here: a zero-norm corpus alerts, a Tesla-shaped empty alarm collection does
not.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from plant_rag.scheduling.watchdog import (
    DEAD,
    OK,
    SAMPLE_POINTS,
    CorpusWatchdog,
    check,
)


class FakeClock:
    """Deterministic clock the tests drive explicitly."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeQdrant:
    """In-memory stand-in for exactly the calls the watchdog makes.

    Alias resolution and the revision filter are applied inside the fake
    (mirroring one Qdrant scroll), and each scroll is recorded so a test can
    assert the probe stayed bounded.
    """

    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.collections: dict[str, list[dict]] = {}
        self.scroll_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.fail_with: Exception | None = None

    # --- client API -----------------------------------------------------

    def get_aliases(self):
        return SimpleNamespace(
            aliases=[
                SimpleNamespace(alias_name=alias, collection_name=target)
                for alias, target in self.aliases.items()
            ]
        )

    def count(self, collection_name, count_filter=None, exact=True, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.count_calls.append(
            {"collection": collection_name, "filter": count_filter}
        )
        revision = self._revision(count_filter)
        points = [
            point
            for point in self.collections.get(collection_name, [])
            if revision is None
            or point["payload"].get("revision") == revision
        ]
        return SimpleNamespace(count=len(points))

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=10,
        offset=None,
        with_payload=True,
        with_vectors=False,
    ):
        if self.fail_with is not None:
            raise self.fail_with
        self.scroll_calls.append(
            {
                "collection": collection_name,
                "limit": limit,
                "with_vectors": with_vectors,
            }
        )
        revision = self._revision(scroll_filter)
        points = [
            point
            for point in self.collections.get(collection_name, [])
            if revision is None
            or point["payload"].get("revision") == revision
        ]
        start = int(offset) if offset is not None else 0
        window = points[start : start + limit]
        next_offset = start + limit if start + limit < len(points) else None
        records = [
            SimpleNamespace(
                id=point["id"],
                payload=point["payload"],
                vector=point["vector"],
            )
            for point in window
        ]
        return records, next_offset

    # --- test helpers ---------------------------------------------------

    @staticmethod
    def _revision(filter_obj):
        if filter_obj is None:
            return None
        for condition in getattr(filter_obj, "must", None) or []:
            if getattr(condition, "key", None) == "revision":
                return condition.match.value
        return None

    def add_text(self, revision: str, vectors: list[list[float]]) -> str:
        name = f"manual_{revision}_text"
        self.collections.setdefault(name, []).extend(
            {
                "id": f"{name}-{index}",
                "payload": {"revision": revision},
                "vector": vector,
            }
            for index, vector in enumerate(vectors)
        )
        return name

    def add_alarms(self, revision: str, rows: int) -> str:
        name = f"manual_{revision}_alarms"
        self.collections.setdefault(name, []).extend(
            {
                "id": f"{name}-{index}",
                "payload": {"revision": revision, "alarm_code": "1000"},
                "vector": None,
            }
            for index in range(rows)
        )
        return name

    def swap(self, revision: str) -> None:
        self.aliases["current_text"] = f"manual_{revision}_text"
        self.aliases["current_alarms"] = f"manual_{revision}_alarms"


def signal(index: int) -> list[float]:
    """A vector that carries signal (a single nonzero component is enough)."""
    return [0.0] * index + [1.0]


def zero_vector() -> list[float]:
    return [0.0] * 4


def healthy_client(alarms: int = 0) -> FakeQdrant:
    client = FakeQdrant()
    client.add_text("r1", [signal(1), signal(2)])
    if alarms:
        client.add_alarms("r1", alarms)
    client.swap("r1")
    return client


# --- verdicts ---------------------------------------------------------------


def test_healthy_corpus_is_alive_and_quiet(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    health = check(healthy_client())

    assert health.status == OK
    assert health.alive is True
    assert health.revision == "r1"
    assert health.text_points == 2  # the EXACT revision-scoped count
    assert health.nonzero_vectors == 2
    assert health.reasons == ()
    assert health.message_es() is None
    assert caplog.records == []


def test_zero_norm_vectors_are_dead_and_alert(caplog: pytest.LogCaptureFixture) -> None:
    # The live incident: a zero-vector encoder indexed, aliased, and then
    # answered every question with the exact refusal.
    caplog.set_level(logging.WARNING)
    client = FakeQdrant()
    client.add_text("r1", [zero_vector(), zero_vector(), zero_vector()])
    client.swap("r1")

    health = check(client)

    assert health.status == DEAD
    assert health.alive is False
    assert health.nonzero_vectors == 0
    assert any("norma cero" in reason for reason in health.reasons)
    message = health.message_es()
    assert message is not None and "reindexar" in message


def test_one_healthy_vector_is_enough() -> None:
    # The probe is a sample, not a scan: one vector with signal proves the
    # encoder wrote signal, so a partly-zero corpus is NOT dead.
    client = FakeQdrant()
    client.add_text("r1", [zero_vector(), signal(3), zero_vector()])
    client.swap("r1")

    health = check(client)

    assert health.status == OK
    assert health.sampled_vectors == 3
    assert health.nonzero_vectors == 1


def test_empty_text_collection_is_dead() -> None:
    client = FakeQdrant()
    client.add_text("r1", [])
    client.swap("r1")

    health = check(client)

    assert health.status == DEAD
    assert any("no contiene filas" in reason for reason in health.reasons)


def test_missing_alias_is_dead() -> None:
    # Nothing ingested: every question refuses, which is exactly what the
    # operator must be told about instead of discovering it question by question.
    health = check(FakeQdrant())

    assert health.status == DEAD
    assert health.revision is None
    assert any("current_text" in reason for reason in health.reasons)


def test_alias_outside_the_collection_contract_is_dead() -> None:
    client = FakeQdrant()
    client.collections["stray"] = [
        {"id": "p1", "payload": {"revision": "r1"}, "vector": signal(1)}
    ]
    client.aliases["current_text"] = "stray"

    health = check(client)

    assert health.status == DEAD
    assert any("fuera del contrato" in reason for reason in health.reasons)


def test_rows_of_another_revision_do_not_count_as_a_live_corpus() -> None:
    # Defense in depth behind the single-current-alias invariant: a stray point
    # from an older revision is not evidence that THIS revision is alive.
    client = FakeQdrant()
    client.add_text("r1", [signal(1)])
    client.add_text("r2", [])
    client.aliases["current_text"] = "manual_r2_text"
    client.aliases["current_alarms"] = "manual_r2_alarms"

    health = check(client)

    assert health.status == DEAD
    assert health.revision == "r2"
    assert any("no contiene filas" in reason for reason in health.reasons)


# --- dormant by design (the Tesla case) -------------------------------------


def test_empty_alarm_collection_is_dormant_not_dead(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # ACCEPTANCE: a Tesla-shaped corpus (healthy text, zero alarm rows) must
    # NOT alert. Alphanumeric alerts legitimately yield no alarm table.
    caplog.set_level(logging.WARNING)
    client = FakeQdrant()
    client.add_text("r1", [signal(1)])
    client.add_alarms("r1", 0)
    client.swap("r1")

    health = check(client)

    assert health.status == OK
    assert health.alarm_table is False
    assert health.alarm_collection == "manual_r1_alarms"
    assert health.alarm_points == 0
    assert health.reasons == ()
    assert caplog.records == []


def test_missing_alarm_alias_is_still_dormant() -> None:
    # A text-only corpus (no alarm collection at all) stays OK: dormancy is
    # reported, never treated as a fault.
    client = FakeQdrant()
    client.add_text("r1", [signal(1)])
    client.aliases["current_text"] = "manual_r1_text"

    health = check(client)

    assert health.status == OK
    assert health.alarm_collection is None
    assert health.alarm_table is False


def test_alarm_rows_are_reported_when_present() -> None:
    health = check(healthy_client(alarms=3))

    assert health.status == OK
    assert health.alarm_table is True
    assert health.alarm_points == 3


# --- the probe is bounded and read-only -------------------------------------


def test_probe_never_requests_more_than_the_sample_bound() -> None:
    client = healthy_client()
    check(client)

    assert client.scroll_calls
    assert all(call["limit"] <= SAMPLE_POINTS for call in client.scroll_calls)


def test_reported_point_counts_are_exact_not_the_sample_size() -> None:
    # A sample of 64 rows in a 300-row corpus must report 300, not 64: a floor
    # presented as a count is a wrong number.
    client = FakeQdrant()
    client.add_text("r1", [signal(index) for index in range(300)])
    client.add_alarms("r1", 3)
    client.swap("r1")

    health = check(client)

    assert health.status == OK
    assert health.sampled_vectors == SAMPLE_POINTS  # bounded probe
    assert health.text_points == 300  # exact count
    assert health.alarm_points == 3


# --- scheduling hook --------------------------------------------------------


def test_watchdog_throttles_to_the_interval_and_reprobes_afterwards() -> None:
    clock = FakeClock()
    client = healthy_client()
    watchdog = CorpusWatchdog(client, interval_s=60.0, clock=clock)

    def reads() -> int:
        """Every Qdrant read the probe made so far (scroll + exact count)."""
        return len(client.scroll_calls) + len(client.count_calls)

    first = watchdog.maybe_check()
    assert first is not None and first.status == OK
    after_first = reads()
    assert after_first > 0

    clock.advance(30.0)
    assert watchdog.maybe_check() is None  # still inside the interval
    assert reads() == after_first  # throttled: no probe ran at all

    clock.advance(31.0)
    again = watchdog.maybe_check()
    assert again is not None
    assert reads() > after_first
    assert watchdog.last_health is again


def test_watchdog_reports_a_transport_failure_without_raising() -> None:
    # A dead Qdrant is a dead corpus; the loop must keep running.
    client = healthy_client()
    client.fail_with = ConnectionError("qdrant unreachable")
    watchdog = CorpusWatchdog(client, interval_s=1.0, clock=FakeClock())

    health = watchdog.maybe_check()

    assert health is not None
    assert health.status == DEAD
    assert any("falló" in reason for reason in health.reasons)


def test_watchdog_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="interval_s"):
        CorpusWatchdog(healthy_client(), interval_s=0)


def test_check_now_alerts_on_a_dead_corpus(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    watchdog = CorpusWatchdog(FakeQdrant(), clock=FakeClock())

    health = watchdog.check_now()

    assert health.status == DEAD
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
