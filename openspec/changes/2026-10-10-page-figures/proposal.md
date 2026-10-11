# Proposal: Page figures beside the cited excerpt (phase-1 `[IMAGEN]` handling)

## Intent

The original design deferred images explicitly (`raw/articles/diseno/2026-10-09-proposal.md`:
*"Out of Scope: Haas images / `[IMAGEN]` handling"*; `Caso de uso.txt`: *"luego
metes el Haas de 187 páginas con imágenes y añades la lógica de `[IMAGEN: Pág X]`"*).
Operators read procedure manuals whose answers are often a diagram, so this
change brings a first, honest slice: the page's figure is shown BESIDE the
cited excerpt, and only when "procede" (the cited page actually has one).

## Decisions (heuristic, no model — the key constraint)

- **No vision model, no download, no VRAM.** PyMuPDF (already pinned, ADR-002)
  extracts the largest embedded image of each flagged page; the web renders it
  with `st.image` (already a dependency). The 16GB/8GB budget is untouched — a
  VLM would have competed with `qwen2.5:7b` for the 5.6/8GB VRAM already spent
  serving, which is the same reason Qwen 14B was rejected.
- **"Cuando procede" is a file-existence check, not a judgement call.** An
  answer shows a page's figure iff (a) the page's ingested payload carried
  `has_figures` and (b) the PNG exists on disk. No model decides relevance.
- **The figure is a trust aid, never evidence.** It rides beside the excerpt;
  it never enters the prompt, the answer, or the citation contract.

## Evidence / limits

- Extraction and the web path are covered hermetically with PDFs carrying real
  embedded images (PyMuPDF-generated, no model).
- Known limitation: vector-only diagrams (no raster) are invisible to
  `get_images` and stay text-only. The real manual's PDF was not available in
  this environment, so the end-to-end run on the production corpus is not
  exercised here (re-ingesting would also clobber the serving revision).

## Scope

### In Scope

- `config.py`: `FIGURES_DIR` (default `data/figures`, `""` disables).
- `ingest/loader.py`: `Page.has_figures` (cheap flag; no bytes, RSS bound kept).
- `ingest/figures.py` (new): PyMuPDF-only extraction + deterministic paths.
- `ingest/reindex.py`: save figures best-effort before the swap; add the
  `has_figures` payload flag.
- `web/app.py`: show the figure inside a cited page's excerpt when it exists.
- Specs: manual-ingestion + answering deltas; hermetic tests.

### Out of Scope

- Vector-diagram rasterisation, OCR of text inside figures, figure cropping to
  a bounding box, and any vision model or relevance judgement.
- Multi-manual (the figure path already carries `manual_id`/`revision`, so it
  composes with a future multi-manual change).

## Risks

| Risk | Mitigation |
|---|---|
| A page's "figure" is decoration, not the answer | Shown only inside the excerpt the operator opens, never as the answer; the text contract still governs |
| Disk grows with one PNG per image page | Only flagged pages are saved; render fallback is 110 dpi |
| Extraction breaks an ingest | Best-effort: a per-page or save failure only omits the figure; the revision still swaps (test pins it) |

## Rollback

Set `FIGURES_DIR=""` (no code change) or revert the module + wiring; the
`has_figures` payload is inert for every existing reader.
