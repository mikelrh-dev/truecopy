"""Cross-lingual query expansion: Spanish operator words, English manual words.

Conventions established here:

* Expansion exists for RETRIEVAL ONLY. The embedding text and the hybrid
  text branch see the expanded string; the reranker pair and the LLM prompt
  keep the operator's ORIGINAL question — the model must answer what was
  asked, in Spanish, not what a glossary produced.
* The glossary is a CLOSED, accent-insensitive, word-boundary map. It is
  deliberately additive: the question is never rewritten, only followed by
  the English terms the manual would use ("presión neumáticos" ->
  "presión neumáticos tire tires pressure").
* Additive expansion cannot invent evidence: a term the corpus does not
  carry simply matches nothing, while a term it does carry rescues queries
  whose Spanish shares no token with the English manual. That is the exact
  failure dense-only retrieval had on this corpus.
* Appended terms are capped: a kitchen-sink question must not drown its own
  embedding under twenty synonyms.
"""

from __future__ import annotations

import re
import unicodedata

# Curated ES -> EN technical glossary (automotive + machining manuals).
# Extending it is a product decision; matching is word-boundary and
# accent-insensitive, so plural/accents are enumerated here once.
GLOSSARY_ES_EN: dict[str, tuple[str, ...]] = {
    "neumatico": ("tire", "tyre"),
    "neumaticos": ("tires", "tyres"),
    "rueda": ("wheel",),
    "ruedas": ("wheels",),
    "presion": ("pressure",),
    "aceite": ("oil",),
    "filtro": ("filter",),
    "filtros": ("filters",),
    "freno": ("brake",),
    "frenos": ("brakes",),
    "frenado": ("braking",),
    "bateria": ("battery",),
    "carga": ("charging", "charge"),
    "cargar": ("charging", "charge"),
    "cargador": ("charger",),
    "puerta": ("door",),
    "puertas": ("doors",),
    "cinturon": ("belt",),
    "volante": ("steering", "wheel"),
    "direccion": ("steering",),
    "claxon": ("horn",),
    "luz": ("light",),
    "luces": ("lights",),
    "intermitente": ("turn", "signal"),
    "limpiaparabrisas": ("wipers", "windshield"),
    "parabrisas": ("windshield",),
    "capo": ("hood",),
    "maletero": ("trunk",),
    "remolque": ("towing", "tow"),
    "remolcar": ("towing", "tow"),
    "alarma": ("alarm", "warning"),
    "alarmas": ("alarms",),
    "sensor": ("sensor",),
    "sensores": ("sensors",),
    "temperatura": ("temperature",),
    "refrigerante": ("coolant",),
    "liquido": ("fluid",),
    "marcha": ("gear",),
    "velocidad": ("speed",),
    "acelerador": ("accelerator",),
    "pedal": ("pedal",),
    "asiento": ("seat",),
    "asientos": ("seats",),
    "espejo": ("mirror",),
    "espejos": ("mirrors",),
    "ventana": ("window",),
    "ventanas": ("windows",),
    "techo": ("roof",),
    "llave": ("key",),
    "mando": ("control", "controls"),
    "pantalla": ("screen", "touchscreen"),
    "actualizacion": ("update",),
    "modo": ("mode",),
    "emergencia": ("emergency",),
    "incendio": ("fire",),
    "humo": ("smoke",),
    "cadena": ("chain",),
    "cadenas": ("chains",),
    "hielo": ("ice",),
    "nieve": ("snow",),
    "invierno": ("winter",),
    "aparcamiento": ("parking", "park"),
    "estacionamiento": ("parking", "park"),
    "fusible": ("fuse",),
    "fusibles": ("fuses",),
    "voltaje": ("voltage",),
    "corriente": ("current",),
    "mantenimiento": ("maintenance",),
    "revision": ("service",),
    "intervalo": ("interval",),
    "hidraulico": ("hydraulic",),
    "hidraulica": ("hydraulic",),
    "lubricacion": ("lubrication", "lubricant"),
    "lubricante": ("lubricant",),
    "grasa": ("grease",),
    "fuga": ("leak",),
    "fugas": ("leaks",),
    "ruido": ("noise",),
    "ruidos": ("noise",),
    "vibracion": ("vibration",),
    "vibraciones": ("vibration",),
    "desgaste": ("wear",),
    "valvula": ("valve",),
    "valvulas": ("valves",),
    "bomba": ("pump",),
    "bombas": ("pumps",),
    "deposito": ("reservoir", "tank"),
    "nivel": ("level",),
    "rellenar": ("refill",),
    "purga": ("bleed",),
    "engranaje": ("gear",),
    "rodamiento": ("bearing",),
    "cojinete": ("bearing",),
    "junta": ("gasket", "seal"),
    "juntas": ("gaskets", "seals"),
    "manguera": ("hose",),
    "mangueras": ("hoses",),
    "correa": ("belt",),
    "correas": ("belts",),
    "desbloquear": ("unlock",),
    "bloqueo": ("lock",),
    "bloqueos": ("locks",),
    "manilla": ("handle",),
    "anclaje": ("anchor", "tether"),
    "simbolo": ("symbol", "indicator", "warning"),
    "simbolos": ("symbols", "indicators", "warning"),
    "indicador": ("indicator",),
    "indicadores": ("indicators",),
    "testigo": ("warning", "light"),
    "testigos": ("warning", "lights"),
    "piloto": ("indicator", "light"),
    "cuadro": ("instrument", "cluster", "dashboard"),
    "tablero": ("dashboard", "instrument", "panel"),
    "salpicadero": ("dashboard",),
    "seguridad": ("safety",),
    "peligro": ("danger", "warning"),
    "advertencia": ("warning", "caution"),
    "precaucion": ("caution",),
}

# Cap on appended English terms: keeps a verbose question from drowning its
# own embedding under synonyms.
_MAX_EXPANSION_TERMS = 8


def _normalize(text: str) -> str:
    """Casefold, strip accents, and collapse whitespace for glossary matching."""
    decomposed = unicodedata.normalize("NFD", text.casefold())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents).strip()


def expansion_terms(question: str) -> tuple[str, ...]:
    """English glossary terms the question's Spanish words imply.

    Order follows the glossary (deterministic), capped at
    ``_MAX_EXPANSION_TERMS``; duplicates collapse.
    """
    normalized = _normalize(question)
    if not normalized:
        return ()

    found: list[str] = []
    for spanish, english in GLOSSARY_ES_EN.items():
        if re.search(rf"(?<!\w){re.escape(spanish)}(?!\w)", normalized):
            for term in english:
                if term not in found:
                    found.append(term)
    return tuple(found[:_MAX_EXPANSION_TERMS])


def expand_query(question: str) -> str:
    """The question followed by the English terms the manual would use.

    Additive and identity-safe: a question with no glossary hits is returned
    unchanged, and the original text always comes first.
    """
    terms = expansion_terms(question)
    if not terms:
        return question
    return f"{question} {' '.join(terms)}"


# Spanish question scaffolding and articles that add nothing to a retrieval
# query: an operator asks "¿cómo hago X?" while the manual states "To do X".
_SPANISH_QUESTION_STOPWORDS = frozenset(
    {
        "como",
        "qué",
        "que",
        "cual",
        "cuál",
        "cuando",
        "cuándo",
        "donde",
        "dónde",
        "quien",
        "quién",
        "cuanto",
        "cuánto",
        "cuantos",
        "cuántos",
        "para",
        "por",
        "porque",
        "hago",
        "hace",
        "hacer",
        "debo",
        "debería",
        "deberia",
        "puedo",
        "puede",
        "pueden",
        "tengo",
        "tiene",
        "tienen",
        "hay",
        "es",
        "son",
        "esta",
        "está",
        "este",
        "esto",
        "los",
        "las",
        "un",
        "una",
        "unos",
        "unas",
        "del",
        "al",
        "lo",
        "la",
        "el",
        "y",
        "o",
        "en",
        "con",
        "sin",
        "sobre",
        "mi",
        "su",
        "sus",
        "me",
        "se",
        "si",
        "sí",
    }
)


def keyword_variant(question: str) -> str | None:
    """The question reduced to its content words, or ``None`` if nothing remains.

    This is the SECOND retrieval query when it differs from the first: the
    full question retrieves well for conversational manuals, the keyword
    variant for terse tables. When a question is already keywords
    ("presion neumaticos") the variant is ``None`` and no second query runs —
    multi-query must stay free when it cannot help.
    """
    normalized = _normalize(question)
    words = [w for w in re.split(r"[^\w]+", normalized) if w]
    keywords = [w for w in words if w not in _SPANISH_QUESTION_STOPWORDS]
    if not keywords:
        return None
    variant = " ".join(keywords)
    return None if variant == normalized else variant
