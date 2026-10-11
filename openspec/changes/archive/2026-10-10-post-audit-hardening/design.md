# Design: Post-audit hardening

## Technical Approach

Four additive fixes on existing seams, no new module and no new dependency.

```
identity:   filename ──sanitize──▶ manual_id            (one namespace)
            CLI ingest ─┐
            CLI reindex ├─▶ manual_id_from_filename(name)
            web upload ─┘        (staged path stays a UUID)

provenance: answer_question ──current_revision(client)──▶ "r{N}" ──▶ build_record(revision=)

advisory:   after a successful CLI reindex ──▶ calibration.advisory(path, id, revision)

eval note:  --record that appended nothing ──▶ reason on STDERR   (stdout frozen)
```

## Decision record

| # | Decision | Chosen | Rejected | Rationale |
|---|----------|--------|----------|-----------|
| 1 | Identity source | The manual's filename, sanitized by ONE helper | Per-entrypoint derivation; a shared constant | Golden sets and calibration already key on the PDF stem; one helper makes "same file, same identity" true across CLI and web |
| 2 | Where the helper lives | `ingest/reindex.py` (the shared ingestion module) | A new `identity.py`; duplicated logic in each script | The helper belongs to the ingestion capability and avoids a module for one function |
| 3 | Sanitization | `re.sub(r"[^0-9A-Za-z_]+", "_", stem).strip("_")`, fallback `"manual-sin-nombre"` | Reusing the UUID-staged path | The path is deliberately opaque for traversal safety; identity must come from the name |
| 4 | Historical `manual-actual` records | Left alone; a miss stays a clean "no record" | Fuzzy-match or migrate them | The old key never matched a real manual; a silent fuzzy match would be worse than a miss |
| 5 | When to read the revision | Once per question when a log exists; reuse the cache path's read | Read on every branch; add it to the cache key | The cache path already reads it; a second read would be waste. The cache key stays (revision, model, question) — multi-manual owns adding `manual_id` |
| 6 | `--record` diagnostics | Reason on stderr, stdout byte-identical | Fold the reason into stdout | The eval stdout is a frozen, test-pinned contract |

## Sequencing

| Step | Edit | Focused test |
|------|------|--------------|
| 1 | `manual_id_from_filename` + CLI/web wiring | `tests/test_owner_ops.py`, `tests/test_ingest.py` |
| 2 | `build_record(revision=)` + `pipeline` stamp | `tests/test_pipeline.py` |
| 3 | reindex-CLI advisory | `tests/test_ingest.py` |
| 4 | eval `--record` stderr note | `tests/test_scripts.py` |

## Failure modes

| Failure | Behavior |
|---|---|
| Upload named `../../etc/passwd.pdf` | Identity is `passwd`; the staged path stays under the staging dir (UUID name) |
| Calibration store disabled | No advisory printed; no file read |
| `current_revision` unreachable while logging | Not on the answer path: the log is best-effort, the answer already returned |
| `--record` with an empty sweep | Nothing appended; the reason prints on stderr |
