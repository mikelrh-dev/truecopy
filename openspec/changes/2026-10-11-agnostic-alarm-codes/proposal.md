# Agnostic alarm codes (parked)

Status: parked for maintainer review. No implementation approved.

## The misconception

The alarm fast path assumes every manual uses Siemens-style numeric alarm
tables: codes are exactly 4–6 digits (`^\d{4,6}$` in `ingest/alarms.py`,
`retrieve/fastpath.py`, and both row validators). That shape was never a
universal truth — it was one manufacturer's convention promoted to a
system-wide contract.

## Evidence (2026-10-11, live Qdrant)

- `current_alarms` → `manual_r12_alarms`; revisions r8/r10/r11/r12 alarm
  collections all hold **0 points**.
- r10/r11/r12 index `manual_id: "Owners_Manual"` (Tesla Model 3). Its alerts
  are alphanumeric (`CP_a151`, `CC_a011`), so extraction correctly yields
  zero rows and `has_alarm_table()` returns `False`.
- Consequence today: digit-shaped queries route to semantic search (gate E3).
  Nothing is broken; the exact path is dormant, not dead.

## Why it matters

If TrueCopy stays industrial-machinery-only, numeric-only is fine. If it must
stay manual-agnostic (Tesla today, others tomorrow), the hardcoded shape
silently disables the exact path for every non-numeric manual — with no
warning to the owner.

## Options considered (no decision)

1. **Per-revision pattern, owner-confirmed** (recommended): ingest proposes a
   code pattern per manual, the owner confirms at reindex (same
   inform/apply split as calibration), the pattern lives in the index — never
   in config — and the fast path reads the current revision's pattern.
2. **Global laxer regex**: cheap, rejected — years, voltages and page numbers
   would poison the alarms collection and cause false exact refusals.
3. **Do nothing**: acceptable if semantic search covers Tesla alerts well;
   gives up the exact path outside Siemens-style manuals.

## Open questions for review

- Are Tesla alerts laid out in tables (first-cell codes) or running text?
  If text-only, line-pattern extraction needs extending too, not just the code
  shape.
- Where should a per-revision pattern live in the index (collection metadata
  vs. marker point) so `has_alarm_table()` semantics stay intact?
- Should pattern confirmation ride the existing calibration advisory UI or a
  separate reindex step?
