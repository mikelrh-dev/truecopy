# Proposal: Wiki alignment — RAGProyect

**Date:** 2026-10-11
**Repo HEAD:** `0c20324d05187d2e0fa0cb552f4ad1ebb335bb4d` (Merge PR #6)
**Working tree:** 13 files modified (+635/-38) — post-audit hardening, named volume, cache, feedback, figures
**Suite:** 394 tests collected (hermetic)

## Goal

Align the wiki 100% with the current codebase so it can be used as the single source of truth to understand the project.

## Scope

### A. Fix 8 existing pages (drift correction)

| Page | Changes |
|------|---------|
| `busqueda-hibrida-rrf.md` | Update 5 MOVED line citations (hybrid.py: 22-25→26-33, 51-53→146-154, 65-73→109-116, 75-84→117-126, 95-106→133-144). Add: timeout=10, ValueError validations, RERANK_POOL=20, client-side MIN_SCORE filter in store.search. |
| `cadena-unica-respuesta.md` | Fix 6 out-of-EOF citations (pipeline.py: 398-431→218-335, 422-426→188-190, 440-466→156-215, 460-472→94-104, 474-482→386-395, 483-492→365-379). Fix config.py:71-78→104-106. Fix rerank.py:89-102→100-127. Add: answer cache (pipeline.py:263-285), decline events (pipeline.py:305-311), timings, deadline, retry, fastpath cap=10. |
| `contrato-negativa.md` | Fix 4 STALE citations (llm.py: 198-201→222-224, 225-228→249-253, 229-235→258-266, 226-253/241-253→267-309). Add puerta 0: answer cache bypasses revalidation on HIT. Add: second decline signal, decline events in monitoring. |
| `perimetro-lan-autenticacion.md` | Fix STALE: `users.json` → `.streamlit-users.json` (auth.py:36, app.py:112). Add: AuthStore CRUD, random password bootstrap (auth.py:226-252). |
| `operacion-infraestructura.md` | Fix ERROR: "se resetea desde la UI" → no existe; contraseña se cambia editando `.streamlit-users.json`. Fix STALE: `users.json` → `.streamlit-users.json`, `qdrant_data/` → named volume `ragproyect_qdrant_data`. Fix Rollback: `down -v` + volumen (no bind mount). Add: feedback widget, figuras, `scripts/feedback_report.py`. |
| `puesta-en-marcha.md` | Fix ERROR: "3/3 checks" → "4/4 checks" (eval.py:1003). Fix STALE-SOURCE: vectores-a-cero causa=bind mount, remedio=rebuild_vectors.py. Fix: bloque child_text ya no está en README. Update test count references. |
| `resumen-arquitectura.md` | Add new modules: `answer/cache.py`, `calibration.py`, `ingest/figures.py`, `scripts/feedback_report.py`, `web/ui_es.py`, monitoring events (feedback+decline), timings, warm-up, decline hook. |
| `alcance-sistema.md` | Move from "in de facto" to "Dentro": caché de respuestas, feedback de operario, calibración por manual, figuras por página. Note multi-manual: data model ready, serving still single-manual. |

### B. Create 4 new pages

| Page | Content |
|------|---------|
| `wiki/concepts/cache-respuestas.md` | Answer cache: key=(revisión, modelo, huella config, pregunta normalizada), JSONL append-only + LRU, puerta 0 (HIT → return sin cadena, sin revalidar citas), config knob, wiring CLI/web. |
| `wiki/concepts/calibracion-por-manual.md` | Calibration: append-only records per manual/revisión, eval `--record`, advisory solo lectura tras ingesta, config knob. |
| `wiki/procesos/feedback-operario.md` | Feedback: thumbs widget, eventos en JSONL, informe semanal (`scripts/feedback_report.py`), vista `--signals` de decline. |
| `wiki/concepts/figuras-por-pagina.md` | Figures: extracción PyMuPDF por página, guardado `<FIGURES_DIR>/<revisión>/<manual>/page-N.png`, mostrado junto al excerpt en web, config knob. |

### C. Update infrastructure

- `wiki/index.md` — regenerate with new pages
- `wiki/log.md` — append entry `[2026-10-11] Alineación completa wiki`

### D. New raw sources

| Raw source | Content |
|------------|---------|
| `raw/articles/docs/2026-10-11-wiki-alignment.md` | Code snapshots: cache.py, calibration.py, figures.py, feedback in monitoring, pipeline decline events, timings |

## Decision

**Approved by:** (pending)
**Date:** (pending)

## Next

After approval: apply → regenerate index → validate gates → mark applied.
