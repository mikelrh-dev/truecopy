"""Alarm extraction: ``find_tables()`` rows with a line-regex fallback (ADR-003).

The alarm fast path answers code-shaped queries by exact ``MatchValue`` on
``current_alarms`` — never embeddings — so rows must be extracted with their
code normalized to the exact ``^\d{4,6}$`` shape queries use. Primary source
is PyMuPDF ``find_tables()`` (first cell must be the code); when a page yields
no table rows, a line regex over the extracted text is the fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from plant_rag.ingest.loader import Page, TableGrid

# Exact shape of an alarm code as used by the fast path (ADR-003).
ALARM_CODE_PATTERN = re.compile(r"^\d{4,6}$")

# Fallback: a code at line start followed by a description.
_LINE_PATTERN = re.compile(r"^\s*(\d{4,6})\s+(\S.*?)\s*$")


@dataclass(frozen=True)
class AlarmRow:
    """One alarm as indexed: code for exact match, text/page for the answer."""

    manual_id: str
    page: int
    revision: str
    alarm_code: str
    text: str

    def __post_init__(self) -> None:
        if not self.manual_id.strip():
            raise ValueError("manual_id must not be empty")
        if self.page < 1:
            raise ValueError(f"page must be >= 1, got {self.page}")
        if not self.revision.strip():
            raise ValueError("revision must not be empty")
        if not ALARM_CODE_PATTERN.match(self.alarm_code):
            raise ValueError(
                f"alarm_code must match {ALARM_CODE_PATTERN.pattern}, "
                f"got {self.alarm_code!r}"
            )


def alarms_from_tables(
    grids: list[TableGrid], *, manual_id: str, page: int, revision: str
) -> list[AlarmRow]:
    """Take rows whose FIRST cell is exactly a 4-6 digit code (ADR-003).

    Rows are skipped when the first cell is empty, non-numeric, or longer
    than six digits; the description is the remaining cells joined.
    """
    rows: list[AlarmRow] = []
    for grid in grids:
        for row in grid:
            if not row:
                continue
            first = (row[0] or "").strip()
            if not ALARM_CODE_PATTERN.match(first):
                continue
            description = " ".join(
                cell.strip() for cell in row[1:] if cell and cell.strip()
            )
            rows.append(
                AlarmRow(
                    manual_id=manual_id,
                    page=page,
                    revision=revision,
                    alarm_code=first,
                    text=description,
                )
            )
    return rows


def alarms_from_lines(
    text: str, *, manual_id: str, page: int, revision: str
) -> list[AlarmRow]:
    """Fallback: ``code + description`` lines when the page has no alarm table."""
    rows: list[AlarmRow] = []
    for line in text.splitlines():
        match = _LINE_PATTERN.match(line)
        if match:
            rows.append(
                AlarmRow(
                    manual_id=manual_id,
                    page=page,
                    revision=revision,
                    alarm_code=match.group(1),
                    text=match.group(2),
                )
            )
    return rows


def extract_alarms(page: Page, *, manual_id: str, revision: str) -> list[AlarmRow]:
    """Extract alarm rows from one page.

    Table rows win when present (``find_tables()`` first-cell rule); the
    line-regex fallback only runs when the page yields no table alarms.
    """
    rows = alarms_from_tables(
        list(page.tables), manual_id=manual_id, page=page.number, revision=revision
    )
    if rows:
        return rows
    return alarms_from_lines(
        page.text, manual_id=manual_id, page=page.number, revision=revision
    )
