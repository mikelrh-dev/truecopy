# Arquitectura del sistema

Resumen de cómo está construido el asistente RAG local: componentes, flujo de
una pregunta, decisiones de diseño y dónde vive cada cosa.

---

## 1. Vista general

```
                    ┌─────────────────────────── RED LOCAL ───────────────────────────┐
                    │                                                                  │
  manual PDF ──▶ INGESTA ──▶ QDRANT (vectores + texto) ──▶ RECUPERACIÓN ──▶ RESPUESTA │
   (inglés)        PyMuPDF     colección por revisión       híbrido RRF       LLM local │
                   lotes       alias "current_*"            glosario ES→EN    contrato  │
                   100 pág     índice texto child_text      reranker CPU      de cita    │
                                                                                    │
  operario ──▶ WEB (login, cola, métricas) ──▶ COLA ──▶ pipeline compartido ◀─────────┘
   (español)     Streamlit       prioridad                      │
                   Argon2         seguridad              mismo camino que
                                   45 s timeout          CLI, web y eval
```

Todo corre en tu PC (16 GB RAM / 8 GB VRAM). **Nada sale de la red local**:
sin APIs externas, sin nube, en desarrollo ni en producción.

---

## 2. Componentes

### Ingestión (`src/plant_rag/ingest/`)
| Módulo | Qué hace |
|---|---|
| `loader.py` | Lee el PDF por lotes de 100 páginas (acota la RAM). Cada página extrae texto, tablas y su **título de sección** del índice del PDF |
| `chunker.py` | Parte cada página en un chunk padre (la página entera) y ventanas hijas de ~1000 caracteres. Los hijos llevan prefijo `[Sección]` para que la búsqueda acierte por tema; los padres se mantienen literales |
| `alarms.py` | Extrae filas de alarmas (primera celda de 4-6 dígitos) para la vía rápida exacta |
| `reindex.py` | ** azul/verde**: construye la revisión `r{N+1}` al lado de la vigente, valida conteos, mueve los dos alias en una llamada atómica y borra la vieja. Un fallo a mitad deja la anterior sirviendo |

### Almacenamiento (Qdrant, Docker)
- Una **colección por revisión**: `manual_r{N}_text` (vectores de los hijos) y `manual_r{N}_alarms` (solo payload, para el MatchValue exacto).
- Dos **alias** siempre vigentes: `current_text` / `current_alarms`. Toda consulta pasa por ellos, lo que hace el reemplazo atómico.
- Vectores **en disco** (`on_disk`) para no comerse la RAM; índice de **texto** sobre `child_text` que crea la propia ingesta en cada revisión.

### Recuperación (`src/plant_rag/retrieve/`)
| Módulo | Qué hace |
|---|---|
| `expand.py` | Glosario cerrado ES→EN: "presión neumáticos" → añade "tire tires pressure". **Solo para buscar**; el modelo ve tu pregunta original |
| `embedder.py` | BGE-M3 en CPU (2,5 GB RAM, 0 VRAM) |
| `hybrid.py` | Fusión denso + texto (RRF) dentro de Qdrant, con el suelo de relevancia por rama |
| `rerank.py` | Cross-encoder en CPU que reordena el pool y corta al top-5. Si el modelo no carga, degrada al orden de recuperación (nunca rompe) |
| `fastpath.py` | Vía exacta de códigos de alarma: pregunta con forma de código → MatchValue, sin embeddings ni LLM |

### Respuesta (`src/plant_rag/answer/`)
`pipeline.py` es **la única cadena**: fastpath → expansión → multi-query con *backfill* (la variante por palabras clave solo rellena huecos, nunca desplaza) → rerank → LLM. La usan CLI, web y eval: un contrato, tres puertas.

`llm.py` aplica el contrato: puerta de evidencia vacía (sin LLM), opciones fijas (`num_ctx=8192`, `temperature=0.2`), validador de citas, **un** reintento acotado si la cita falló pero existía, y negativa exacta si el modelo declina. `prompt.py` lleva las reglas en español (citar solo etiquetas de extractos, nunca páginas referenciadas) y el conjunto cerrado de términos de seguridad.

### Cola (`src/plant_rag/scheduling/`)
Un único worker, orden por montículo `(antigüedad, seguridad, llegada)`: una consulta de seguridad salta por delante de las rutinas recién llegadas, pero una rutina que lleva ≥15 s esperando gana a una seguridad recién llegada (anti-inanición). El plazo de 45 s gobierna despacho y generación: se agota → aviso español, **nunca** respuesta parcial.

### Web (`web/`)
Streamlit sobre la LAN con login (Argon2id, `users.json` gitignored, flag de propietario). Muestra la respuesta citada, el **extracto** de cada página citada, métricas de sesión y el acumulado histórico. Un **scheduler compartido por proceso** (las sesiones comparten cola de verdad) con enrutado de respuestas por pregunta.

### Monitorización (`src/plant_rag/monitoring.py`)
Log JSONL local (gitignored) con una línea por respuesta: pregunta, tipo, latencia, páginas citadas, modelo. Agrega totales, tasa de éxito útil y percentiles de latencia; la web lo muestra como "acumulado en este equipo".

### Evaluación (`scripts/eval.py`)
Cuatro controles en vivo contra la pila real: alarma conocida/desconocida, cross-lingual ES→EN, negativas fuera de corpus y **goldens por manual** (páginas esperadas, verificadas a mano). El conjunto de goldens del manual cargado se ejecuta; un manual sin goldens **falla a propósito** en vez de aprobarse en silencio. Además barre el umbral de relevancia y lo reporta sin cambiarlo.

---

## 3. Decisiones clave (y por qué)

| Decisión | Motivo |
|---|---|
| Azul/verde con swap de alias | Reindexar sin parar el servicio y sin mezclar corpus; un fallo deja lo anterior sirviendo |
| Padre-hijo con prefijo de sección | Recuperar por tema de página, no por frase suelta; el padre literal es la evidencia que ve el modelo |
| Vectores en disco, embeddings en CPU | Presupuesto 16/8: la VRAM queda entera para el LLM |
| Contrato de cita determinista | El modelo nunca es la fuente de la verdad: una cita fuera del conjunto recuperado es negativa |
| Reintento **acotado** y condicionado | Un 7B falla marcadores a veces; reintentar solo cuando intentó citar (nunca cuando declinó) |
| Multi-query con *backfill* | La variante por palabras clave amplía evidencia pero nunca desplaza lo que la pregunta ya acertó |
| Goldens por manual | El eval debe exponer lo que no puede verificar, no aprobarse a sí mismo |
| Un único camino de respuesta | CLI, web y eval comparten pipeline: un arreglo de contrato aterriza en los tres |

---

## 4. Mapa de ficheros

```
src/plant_rag/
  config.py            todos los ajustes (PLANT_RAG_*), suelo 0.40, plazo 45 s
  models.py            Hit, Citation, Answer, REFUSAL_ES, TIMEOUT_ES
  ingest/              loader, chunker, alarms, reindex (azul/verde)
  retrieve/            expand, embedder, hybrid, rerank, fastpath, store
  answer/              pipeline, prompt, citations, llm, cache
  scheduling/          scheduler (cola prioritaria)
  monitoring.py        log JSONL (respuestas + eventos) y estadísticas
  calibration.py       registros de suelo por manual (eval informa, owner aplica)
web/                   app.py (UI + scheduler compartido), auth.py, ui_es.py
scripts/               ingest, reindex, query, run_web, eval, feedback_report
tests/                 339 tests, todos herméticos (sin Qdrant ni Ollama)
raíz                   README (operación), TUTORIAL (puesta en marcha),
                       COMO-DECLINE-EL-SISTEMA (seguridad), ROADMAP (pendientes),
                       este documento
```

---

## 5. Flujo de una pregunta, de punta a punta

1. El operario escribe en español; la consulta entra en la **cola** con su plazo.
2. Se extrae el código si lo hay → vía exacta de alarmas. Si no, camino semántico.
3. **Expansión** ES→EN y **multi-query** (pregunta + variante por palabras clave, con backfill).
4. Cada consulta recupera por **híbrido** denso+texto sobre la revisión vigente; el suelo descarta lo débil.
5. El **reranker** reordena y corta al top-5.
6. El **LLM** responde en español sobre los extractos etiquetados; su salida pasa el **validador de citas**.
7. La respuesta (citada, negativa exacta o timeout) se registra en el log y se muestra con su extracto.

Más detalle de por qué el paso 6 no se puede saltar el contrato:
`COMO-DECLINE-EL-SISTEMA.md`.
