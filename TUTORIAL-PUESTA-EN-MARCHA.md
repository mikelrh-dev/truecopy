# Puesta en marcha paso a paso — Plant RAG

Guía ordenada para levantar el sistema, indexar el manual y dejarlo probado.
Si algo falla en un paso, no avances al siguiente: cada paso dice qué éxito
esperar.

> Convención: comandos en PowerShell desde la raíz del repo
> (`C:\TrueCopy`). Ajusta la IP y la subred a tu red.

## Paso 0 — Requisitos

- Python 3.11+ instalado (`python --version`).
- Docker Desktop instalado y arrancado.
- Ollama instalado (https://ollama.com).
- El PDF del manual a mano.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Esto tarda un rato (torch CPU + sentence-transformers + bge-reranker-m3).
Éxito esperado: sin errores al final. Comprueba la base:

```powershell
.\.venv\Scripts\python -m pytest tests -q
```

Éxito esperado: `355 passed`. Si falla aquí, el entorno está mal: no sigas.

## Paso 1 — Levantar la infraestructura

```powershell
docker compose up -d
curl -s http://127.0.0.1:6333/readyz
```

Éxito esperado: `all shards are ready`.

```powershell
ollama pull qwen2.5:7b-instruct
curl http://127.0.0.1:11434/api/tags
```

Éxito esperado: la lista de modelos incluye `qwen2.5:7b-instruct`.
Ollama corre en el host, no en Docker, para reservarle toda la VRAM.

## Paso 2 — Indexar el manual

```powershell
.\.venv\Scripts\python scripts/ingest.py .\documentation\828D_DA_1015_en_en-US.pdf
```

Tarda 20-25 minutos (lotes de 100 páginas, BGE-M3 en CPU). Es normal.
Abre el Administrador de tareas y mira el pico de RAM: debe quedar
holgadamente por debajo de 16 GB (diseño: 9-11 GB en pico).

Éxito esperado: el script termina creando la revisión `r1` y moviendo los
alias `current_text` / `current_alarms`. Si falla, no se sirve nada a medias:
la regla es que un fallo nunca deja corpus mezclado.

## Paso 3 — Probar por CLI

Una pregunta de alarma real (cambia el código por uno de tu manual):

```powershell
.\.venv\Scripts\python scripts/query.py "700016"
```

Éxito esperado: respuesta en español citando `[Fuente: … Página N]`.
Prueba también algo que NO esté en el manual. Éxito esperado: exactamente
`No aparece en la documentación`, sin inventar nada.

## Paso 4 — Evaluación automática (los 3 checks obligatorios)

```powershell
.\.venv\Scripts\python scripts/eval.py
```

Comprueba: (1) alarma conocida → página citada / alarma inventada →
negativa exacta; (2) pregunta en español encuentra pasajes en inglés y
responde en español citado; (3) fuera de corpus → negativa exacta.
Además calibra `MIN_SCORE` (informa, no cambia nada solo).

Éxito esperado: `3/3 checks` en verde. Anota las latencias que imprime
(objetivo <5 s, límite contractual 45 s). Si un check falla, no es puesta en
marcha: es un cambio nuevo a corregir.

## Paso 5 — Arrancar la web y probar en LAN

```powershell
.\.venv\Scripts\python scripts/run_web.py
```

En el primer arranque se crea el propietario por defecto (código `admin`).
Entra desde tu propio PC a `http://127.0.0.1:8501`, cambia esa contraseña y
crea los códigos de operario (código + contraseña por persona).

Luego, desde el móvil/tablet conectado a la wifi de planta, abre
`http://<IP-de-tu-PC>:8501`. Éxito esperado: login, preguntas y respuestas
citadas, aviso de posición en cola si hay espera.

## Paso 6 — Cerrar la red (obligatorio antes de planta)

La app escucha en LAN; el cortafuegos es lo que impide accesos de fuera.
En PowerShell como administrador (ajusta la subred a la tuya):

```powershell
New-NetFirewallRule -DisplayName "Plant RAG Streamlit LAN" `
  -Direction Inbound -Protocol TCP -LocalPort 8501 `
  -Action Allow -RemoteAddress 192.0.2.0/24  # ejemplo: pon tu subred LAN
```

Comprueba que no exista otra regla más amplia para el 8501. Sin esto, no hay
puesta en producción.

## Checklist de release (los 5)

Marca cada uno con fecha y resultado antes de entregar a operarios:

- [ ] `eval.py` 3/3 en verde con latencias anotadas.
- [ ] Web por LAN: login + pregunta + respuesta citada desde móvil/tablet.
- [ ] Pico de RAM en ingesta < 16 GB observado.
- [ ] Regla de firewall solo-LAN observada (intento externo cae).
- [ ] Timeout de 45 s y salto de cola de seguridad observados (pregunta de
      seguridad con cola cargada).

## Apagar y volver a encender

```powershell
docker compose down        # conserva los datos indexados
docker compose down -v     # BORRA los datos indexados (reindexar después)
```

Ollama y Qdrant deben estar arriba antes de responder o indexar. `users.json`
y `qdrant_data/` no se commitean nunca (están ignorados por git).

## Si algo falla

- `pytest tests -q` en rojo → entorno roto, reinstala el venv.
- `eval.py` en rojo → no es puesta en marcha, es defecto: anota el check y
  el mensaje exacto para corregirlo como cambio.
- La web no carga desde el móvil → revisa IP, puerto 8501 y regla de
  firewall; prueba primero desde el propio PC.
- Duda de datos → `docker compose down -v` + reingesta desde el PDF vigente
  (siempre un único manual vigente).
- **TODAS las respuestas son "No aparece en la documentación" justo tras
  reiniciar Docker** → los vectores en disco se pusieron a cero (el bind
  mount de Windows pierde los mmap con un apagado sucio; los payloads
  sobreviven, por eso Qdrant sigue "verde"). Compruébalo leyendo un vector:
  si sale todo en 0.0, reindexa (el contrato aguantó: negó en vez de
  inventar):
  ```powershell
  .\.venv\Scripts\python -c "import sys; sys.path.insert(0,'src'); from qdrant_client import QdrantClient; c=QdrantClient(url='http://127.0.0.1:6333'); p,_=c.scroll('manual_rN_text', limit=1, with_vectors=True); print(sum(1 for x in (p[0].vector or []) if abs(x)>1e-9))"
  ```
  Un 0 confirma el diagnóstico; un 1024 dice que los vectores están sanos.
- Respuestas peores tras una reindexación antigua → la revisión puede no
  tener el índice de texto de `child_text` (el híbrido degrada a denso sin
  avisar). Ejecuta el bloque "Revisions built before the text index existed"
  del README: lo recrea sin reindexar (comprueba que imprime
  `child_text index ensured on manual_rN_text`).
- Consultas registradas en `logs/queries.jsonl` (gitignored): una línea por
  respuesta con tipo, latencia y páginas citadas; el panel de métricas de la
  web muestra el acumulado de ese fichero.
