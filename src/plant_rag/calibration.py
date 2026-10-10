"""Per-manual MIN_SCORE calibration records and read-only suggestions.

Eval informs; the owner applies. This module is a pure record/suggestion
seam: it never writes config, never touches the index, and serving never
reads it (the retrieval and answer packages are guarded against importing
it by a test).

Scope of a recorded recommendation — dense branch only: the recommended
floor gates the dense (embedding) branch of hybrid retrieval. By contrast,
the text branch stays ungated, because full-text scores are not cosine
scores and cannot be compared against a cosine floor.

Conventions established here (apply to the whole store):

* Append-only JSONL, one v1 record per line: prior lines are never mutated
  or rewritten (``monitoring.py`` / ``answer/cache.py`` precedent).
* ``""`` as the store path disables reads and writes as silent no-ops —
  the same contract as ``QUERY_LOG_PATH`` / ``ANSWER_CACHE_PATH``.
* Corrupt lines are skipped on read, never fatal.
* Timestamps are ISO-8601 UTC with the ``+00:00`` offset (never ``Z``:
  ``datetime.fromisoformat`` on the active interpreter rejects ``Z``).
* Staleness downgrades, never hides: a stale record is still shown with
  its counts, revision, and date, labeled ``soft-stale`` / ``hard-stale``.
* Stdlib only; paths, revisions, and cache paths arrive as parameters so
  this module never imports ``config`` (hermetic seam).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Mapping, Sequence

#: Age after which a matching record is downgraded to ``soft-stale``.
SOFT_STALE_AFTER = timedelta(days=90)

_FRESH = "fresh"
_SOFT_STALE = "soft-stale"
_HARD_STALE = "hard-stale"

_SWEEP_KEYS = ("threshold", "in_hits", "out_hits")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def build_record(
    manual_id: str,
    revision: str,
    *,
    current_min_score: float,
    in_corpus_hits: int,
    in_total: int,
    out_of_corpus_hits: int,
    out_total: int,
    recommended_min_score: float | None,
    golden_pass: int | None,
    golden_total: int | None,
    golden_set_present: bool,
    sweep: Sequence[Mapping[str, object]],
    source: str = "eval",
    recorded_at: str | None = None,
) -> dict:
    """Build one v1 calibration record — pure, no I/O.

    ``recommended_min_score`` is ``None`` iff no swept threshold separates
    the labeled set; ``separates_labeled_set`` mirrors that. ``in_corpus_hits``
    / ``out_of_corpus_hits`` are the hits measured at the recommended floor
    (or at the current floor when nothing separates), totals are the size of
    the labeled set. ``sweep`` items carry ``threshold`` / ``in_hits`` /
    ``out_hits`` and are normalized to exactly those keys.
    """
    return {
        "manual_id": manual_id,
        "revision": revision,
        "recommended_min_score": recommended_min_score,
        "current_min_score": current_min_score,
        "in_corpus_hits": in_corpus_hits,
        "in_total": in_total,
        "out_of_corpus_hits": out_of_corpus_hits,
        "out_total": out_total,
        "golden_pass": golden_pass,
        "golden_total": golden_total,
        "golden_set_present": golden_set_present,
        "sweep": [
            {
                "threshold": float(point["threshold"]),
                "in_hits": int(point["in_hits"]),
                "out_hits": int(point["out_hits"]),
            }
            for point in sweep
        ],
        "separates_labeled_set": recommended_min_score is not None,
        "recorded_at": recorded_at or _now().isoformat(),
        "source": source,
    }


def append_record(path: str | Path, record: Mapping[str, object]) -> bool:
    """Byte-append one record as a JSONL line; ``""`` (or failure) → ``False``.

    Best-effort by contract: a recording failure never breaks the caller
    (the eval exit-code contract belongs to the checks, not to the store).
    """
    if not path:
        return False
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 - recording must never break the caller
        return False


def read_records(path: str | Path) -> list[dict]:
    """Every parseable v1 record; ``""``/missing/unreadable reads as empty."""
    if not path:
        return []
    parsed: list[dict] = []
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue  # corrupt line: skipped, never fatal
                if isinstance(data, dict):
                    parsed.append(data)
    except OSError:
        return []
    return parsed


def latest_record(path: str | Path, manual_id: str) -> dict | None:
    """The newest record for ``manual_id``, keyed verbatim (no fuzzy match).

    Newest = highest ``recorded_at``; a tie keeps the LAST line written.
    """
    best: dict | None = None
    best_key: str | None = None
    for record in read_records(path):
        if record.get("manual_id") != manual_id:
            continue
        key = str(record.get("recorded_at", ""))
        if best is None or key >= best_key:
            best, best_key = record, key
    return best


def staleness(
    rec: Mapping[str, object],
    *,
    current_revision: str,
    current_label_totals: Mapping[str, int] | None = None,
    now: datetime | None = None,
) -> str:
    """``fresh`` / ``soft-stale`` / ``hard-stale`` — hard beats soft.

    Hard: revision mismatch or label-total drift (the labeled set the
    recommendation was measured against changed). Soft: the record is
    older than 90 days. Anything else is fresh.
    """
    if rec.get("revision") != current_revision:
        return _HARD_STALE
    if current_label_totals is not None:
        recorded_totals = {
            "in_total": rec.get("in_total"),
            "out_total": rec.get("out_total"),
        }
        if dict(current_label_totals) != recorded_totals:
            return _HARD_STALE
    recorded_at = rec.get("recorded_at")
    if recorded_at:
        try:
            stamp = datetime.fromisoformat(str(recorded_at))
        except ValueError:
            stamp = None
        if stamp is not None:
            reference = _as_aware(now) if now is not None else _now()
            if reference - _as_aware(stamp) > SOFT_STALE_AFTER:
                return _SOFT_STALE
    return _FRESH


def suggest(
    rec: Mapping[str, object],
    label: str,
    *,
    cache_path: str,
) -> str:
    """One Spanish suggestion line carrying the full content contract.

    Every line shows both floors, revision, record date, corpus counts,
    golden counts, and the staleness ``label``; the verbatim identifier
    ``separates this labeled set`` is always present; the answer-cache wipe
    instruction appears iff the recommended floor differs from the current
    one. Read-only: this never writes config, store, or index.
    """
    recommended = rec.get("recommended_min_score")
    current = rec.get("current_min_score")
    if recommended is None:
        floors = (
            "'separates this labeled set': ningún umbral barrido lo cumple; "
            f"suelo actual={current}"
        )
        wipe = ""
    else:
        floors = (
            f"suelo recomendado={recommended} (actual={current}) "
            "separa el conjunto etiquetado ('separates this labeled set')"
        )
        if recommended == current:
            wipe = ""
        elif cache_path:
            wipe = (
                f"; borre la caché de respuestas en {cache_path} para que el "
                "nuevo suelo surta efecto"
            )
        else:
            wipe = "; borre la caché de respuestas para que el nuevo suelo surta efecto"

    golden_pass = rec.get("golden_pass")
    golden_total = rec.get("golden_total")
    if golden_pass is None or golden_total is None:
        golden = "n/d"
    else:
        golden = f"{golden_pass}/{golden_total}"

    if label == _FRESH:
        head = f"Sugerencia de calibración [{label}]"
    else:
        head = f"Aviso [{label}]: registro no vigente"
    return (
        f"{head}: registrado {rec.get('recorded_at')} para {rec.get('manual_id')} "
        f"(revisión {rec.get('revision')}); {floors}; "
        f"en corpus {rec.get('in_corpus_hits')}/{rec.get('in_total')}, "
        f"fuera {rec.get('out_of_corpus_hits')}/{rec.get('out_total')}, "
        f"golden {golden}{wipe}"
    )


def advisory(
    path: str | Path,
    manual_id: str,
    revision: str,
    *,
    cache_path: str,
) -> str | None:
    """The read-only advisory for ``manual_id``; ``None`` iff disabled.

    No record → a brief no-record notice for that id only; a record → the
    suggestion labeled with its staleness against ``revision``.
    """
    if not path:
        return None
    record = latest_record(path, manual_id)
    if record is None:
        return (
            f"No hay registro de calibración para '{manual_id}' — grabe uno con "
            "scripts/eval.py --record."
        )
    label = staleness(record, current_revision=revision)
    return suggest(record, label, cache_path=cache_path)
