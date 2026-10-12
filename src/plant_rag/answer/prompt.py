"""Spanish prompt, closed safety-term set, and safety warning (ADR-004).

Conventions established here:

* The prompt is Spanish (operator language) while the evidence excerpts stay
  in the manual's English — the cross-lingual split the answering spec
  requires. The refusal string is interpolated from ``models.REFUSAL_ES`` so
  the prompt and the contract can never drift apart.
* Safety detection is a CLOSED, rule-based term list — no LLM classifier
  (query-queue spec: "Prioritization SHALL be rule-based over closed terms").
  The same set drives both the queue preemption and the answer warning, so a
  query is "safety" in exactly one sense everywhere.
* Matching normalizes case, accents, and whitespace, but never stems: the set
  enumerates the forms it accepts, which keeps matches auditable.
* Every excerpt is labelled with the exact ``[Fuente: … Página N]`` citation
  format the validator enforces later, so the model copies provenance instead
  of inventing it.
"""

from __future__ import annotations

import re
import unicodedata

from plant_rag.models import REFUSAL_ES, Hit

# Closed safety-term set (normalized, accent-stripped). Both queue
# preemption and the answer warning read this tuple — extending it is a
# product decision, not a tuning knob.
SAFETY_TERMS: tuple[str, ...] = (
    "peligro",
    "peligros",
    "riesgo",
    "riesgos",
    "seguridad",
    "advertencia",
    "advertencias",
    "emergencia",
    "emergencias",
    "evacuacion",
    "alta tension",
    "precaucion",
    "precauciones",
)

# Operator-facing warning appended to grounded answers on safety topics
# (answering spec: "Safety warnings").
SAFETY_WARNING_ES = (
    "Advertencia de seguridad: esta respuesta trata un tema de seguridad. "
    "Verifique siempre el manual original y siga el procedimiento seguro "
    "antes de actuar."
)

# One system message, Spanish, with the contract rules spelled out: ground
# only in the excerpts, cite with the exact marker, copy the evidence
# verbatim, refuse otherwise. The two short examples live INSIDE this single
# system message on purpose: ``build_messages`` keeps the message list at
# ``[system, user]`` (the contract the validator and tests pin), and a
# few-shot pair appended as extra messages would change that shape.
SYSTEM_PROMPT_ES = (
    "Eres el asistente técnico de la máquina para el operador. "
    "Responde siempre en español, de forma breve y operativa.\n"
    "Reglas:\n"
    "1. Usa SOLO los extractos del manual incluidos en el mensaje. "
    "No inventes información ni uses conocimiento externo.\n"
    "2. Cada afirmación que respaldes con un extracto debe llevar su cita "
    "con el formato exacto [Fuente: <manual_id> Página <número>], usando "
    "únicamente las citas proporcionadas en los extractos.\n"
    "3. Copia literalmente de los extractos los datos que reproduzcas: "
    "valores, unidades, códigos y siglas. Traduce al español solo el "
    "texto, nunca las cifras ni las citas.\n"
    "4. Cita SIEMPRE la etiqueta [Fuente: …] del extracto del que tomaste "
    "la información. Si dentro de un extracto se menciona otra página, "
    "NO la cites: cita únicamente la página del extracto usado.\n"
    "5. Si los extractos no cubren la pregunta, responde exactamente: "
    f"{REFUSAL_ES}\n"
    "6. Si usas la misma página varias veces, cítala UNA sola vez (la "
    "primera). Usa como máximo 5 páginas distintas por respuesta.\n"
    "Ejemplo: si un extracto viene etiquetado [Fuente: 828D Página 12] y "
    "dice \"Coolant pressure: 1.2 bar\", responde "
    "\"La presión del refrigerante es 1.2 bar [Fuente: 828D Página 12]\".\n"
    "Ejemplo de rechazo: ante \"¿cuál es el precio de la máquina?\" "
    f"responde exactamente \"{REFUSAL_ES}\"."
)


def _normalize(text: str) -> str:
    """Casefold, strip accents, and collapse whitespace for term matching."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()


def is_safety_query(query: str) -> bool:
    """True when ``query`` contains a term from the closed safety set.

    Word-boundary matching keeps substrings out ("peligrosa" is not
    "peligro"), while normalization keeps operator typing habits in
    ("EVACUACIÓN" and "evacuacion" both match).
    """
    normalized = _normalize(query)
    if not normalized:
        return False
    return any(
        re.search(rf"(?<!\w){re.escape(term)}(?!\w)", normalized)
        for term in SAFETY_TERMS
    )


def build_messages(question: str, hits: tuple[Hit, ...]) -> list[dict]:
    """Ollama chat messages: Spanish system prompt + labelled English excerpts.

    Duplicate ``(manual_id, page, parent)`` passages are collapsed: top-k
    children can map to the same page-sized parent, and repeating it would
    only burn context budget (ADR-004's 8k window) without adding evidence.
    """
    seen: set[tuple[str, int, str]] = set()
    excerpts: list[str] = []
    for hit in hits:
        key = (hit.manual_id, hit.page, hit.parent)
        if key in seen:
            continue
        seen.add(key)
        excerpts.append(f"[Fuente: {hit.manual_id} Página {hit.page}]\n{hit.parent}")
    body = "\n\n".join(excerpts)
    user = f"Extractos del manual:\n\n{body}\n\nPregunta del operador: {question}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT_ES},
        {"role": "user", "content": user},
    ]
