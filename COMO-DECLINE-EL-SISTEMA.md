# Cómo decline el sistema cuando la respuesta no está en el manual

Explicación del mecanismo anti-invención: qué capas hay, qué tuvo que medirse
para elegirlas, y **qué tendría que fallar en cada una** para que el sistema
entregue una respuesta inventada.

---

## 1. La idea en una frase

El sistema **falla cerrado**: cualquier agujero en una capa lo tapa la
siguiente, y el resultado por defecto ante la duda siempre es la negativa
exacta — nunca una respuesta parcial, nunca una invención.

---

## 2. La ruta real, medida

Pregunta usada: **"¿quién fue el presidente de españa en 1995?"**
(historia, no está en el manual de Tesla). Valores medidos en vivo:

| Paso | Qué hace el sistema | Resultado medido |
|---|---|---|
| 1 | ¿Es un código de alarma? (`\b\d{4,6}\b`) | No → camino semántico |
| 2 | Expande con glosario ES→EN | Sin cambios (no hay términos técnicos) |
| 3 | Embebe la consulta (BGE-M3) | vector de 1024 dims |
| 4 | Busca en Qdrant (`current_text`, revisión vigente) | mejores scores: **0.288, 0.261, 0.247** |
| 5 | **Suelo de relevancia 0,40** | **ninguno pasa** |
| 6 | Pool vacío → puerta de evidencia | **negativa exacta, el LLM nunca se invoca** |

Para ver por qué el suelo está donde está (y no es un número a ojo):

- preguntas **dentro** del manual puntúan **0,47–0,67** (medido en varios manuales),
- preguntas **fuera** puntúan **0,31–0,37**.

El suelo (0,40) vive en la ventana entre ambas. Una pregunta sin relación con
el documento se queda sin evidencia antes de llegar al modelo.

Nota del paso 1: el "1995" son 4 dígitos, así que el pipeline lo prueba como
posible código de alarma. Si el manual cargado no tiene ese código (caso del
Tesla: no hay tabla de alarmas) no aporta nada. Si la tuviera, un año podría
colar una fila ajena: es el tradeoff documentado de la unión por mención de
código, y el validador de citas seguiría siendo la red de seguridad.

---

## 3. Las cuatro capas (de la más barata a la más estricta)

1. **Suelo de relevancia** — todo pasaje por debajo de 0,40 se descarta antes
   de existir. Medido, no estimado.
2. **Puerta de evidencia vacía** — si no queda ningún pasaje, la negativa
   exacta se devuelve **sin llamar al LLM**. Al modelo no se le pregunta, no
   puede inventar. La mayoría de negativas salen de aquí.
3. **Validador de citas** — la salida del modelo es **no confiable**. Cada
   `[Fuente: … Página N]` debe (a) parsearse y (b) apuntar a una página que la
   recuperación devolvió de verdad. Cita inventada = página no recuperada =
   negativa. Por eso no puede colarse conocimiento externo disfrazado de cita.
4. **Auto-negativa y reintento acotado** — si el modelo decline (con cita o sin
   ella) es negativa terminal. Si la única cita que escribió falló por una
   errata (un espacio de más), hay **un** reintento corregido: nunca en bucle,
   nunca cuando declinó, nunca por fallos de infraestructura.

Detrás de todo, el plazo de 45 s: un cuelgue produce el aviso de timeout,
**nunca** una respuesta parcial.

---

## 4. Qué tendría que fallar en cada capa

| Capa | Falla si… | Consecuencia | Quién lo cubre |
|---|---|---|---|
| Suelo | pregunta fuera del corpus comparte vocabulario y pasa de 0,40 | entran pasajes basura | el modelo declina con basura delante (medido), y si no, el validador |
| Puerta vacía | nunca falla: es binaria (pool vacío o no) | — | — |
| Validador de citas | el modelo cita una página **recuperada de verdad** que no responde a la pregunta | respuesta inventada con cita válida → **hueco residual real** | `temperature=0.2`, prompt extractivo, extracto visible para verificar en un clic |
| Reintento | presionar al modelo a citar cuando declinó | negativa disfrazada de respuesta (bug real, ya corregido y con test) | test de contrato + check refusal del eval |
| Inyección vía manual | el PDF contiene texto tipo "ignora las instrucciones" | el modelo podría obedecer el texto | el validador sigue exigiendo citas recuperadas; los manuales son de confianza |
| Plazo de 45 s | Ollama se cuelga | aviso de timeout | por construcción: jamás respuesta parcial |
| Corpus | se indexa el PDF **equivocado** | respuestas correctas… del manual equivocado, con citas perfectas | **riesgo operativo, no de IA**: una sola revisión vigente, reindexación por el propietario, campo `revision` |
| Integridad | vectores corruptos (pasó: cero tras un reinicio sucio de Docker) | scores a 0 → todo por debajo del suelo | falló en el sentido **seguro**: negó en vez de inventar |

---

## 5. La conjunción: qué tiene que romperse a la vez

Para que el sistema entregue una respuesta **inventada y convincente** tienen
que coincidir cuatro fallos independientes:

1. que el suelo deje pasar basura, **y**
2. que el modelo no decline teniendo basura delante, **y**
3. que la cita inventada coincida con una página realmente recuperada, **y**
4. que el texto inventado suene plausible.

Y aun entonces, el operario tiene el **extracto citado** bajo la respuesta: la
verificación es un clic, no una investigación.

El diseño no promete "el modelo no se equivoca". Promete:

> **no puede colarte una respuesta que no puedas verificar contra el PDF en un clic.**

---

## 6. Cómo verificarlo tú mismo

Repetir la medición de la sección 2 en cualquier momento:

```powershell
.\.venv\Scripts\python -c "import sys; sys.path.insert(0,'src'); from qdrant_client import QdrantClient; from plant_rag import config; from plant_rag.retrieve.store import current_target, TEXT_ALIAS; from plant_rag.retrieve.embedder import get_embedder; from plant_rag.retrieve.expand import expand_query
q='quien fue el presidente de españa en 1995'
c=QdrantClient(url='http://127.0.0.1:6333'); t=current_target(c, TEXT_ALIAS)
v=get_embedder().encode([expand_query(q)])[0]
r=c.query_points(collection_name=t, query=list(v), limit=5)
print('scores:', [round(p.score,3) for p in r.points], '| suelo =', config.MIN_SCORE)"
```

Un `scores` todo por debajo de 0,40 explica cualquier negativa: el corpus
dijo que no tiene nada que ver con tu pregunta. Y el `eval.py` mantiene estos
mundos vigilados con tres controles automáticos: recuperación de alarma,
cross-lingual y refusals fuera de corpus (más un golden por manual).
