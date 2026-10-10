"""Spanish UI strings for the LAN web app (web-access-auth spec: "Spanish UI").

Conventions established here:

* All operator-facing chrome — labels, placeholders, errors, status messages —
  lives in Spanish here, and the app imports it as a single source of truth.
* Manual excerpts shown to operators remain in their original English; this
  module only owns the chrome around them, never the quoted content.
* The strings are pure data: a module-level dict of literal Spanish text.
  Code/comments stay English so reviewers and future agents read the
  contract without translating prose.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# login gate
# ---------------------------------------------------------------------------

LOGIN_TITLE = "Acceso al asistente de la máquina"

# Shown while the app loads the embedding, reranking and LLM models once per
# process. Without it the first screen would sit blank for ~35s before the
# login gate renders, and the first operator question would pay that load
# inside the 45s answer budget instead.
PREWARM_LABEL = "Preparando el motor de búsqueda (carga de modelos)…"

LOGIN_CODE_LABEL = "Código del operador"
LOGIN_PASSWORD_LABEL = "Contraseña"
LOGIN_SUBMIT = "Entrar"
LOGIN_FORGET = "¿No recuerda el código o la contraseña?"

LOGIN_ERROR_TITLE = "Acceso denegado"
LOGIN_ERROR_GENERIC = "Código o contraseña incorrectos. Inténtelo de nuevo."
LOGIN_ERROR_STORE = "No se puede verificar el acceso. Intente de nuevo más tarde."
LOGIN_ERROR_EMPTY_CODE = "El código no puede estar vacío."
LOGIN_ERROR_EMPTY_PASSWORD = "La contraseña no puede estar vacía."

# ---------------------------------------------------------------------------
# main app chrome
# ---------------------------------------------------------------------------

APP_TITLE = "TrueCopy — Asistente de manuales"

# ---------------------------------------------------------------------------
# question input
# ---------------------------------------------------------------------------

QUESTION_PLACEHOLDER = "Escriba su pregunta en español…"
QUESTION_SEND = "Preguntar"
QUESTION_EMPTY = "Escriba una pregunta antes de continuar."
QUESTION_BUSY = "Ya hay una pregunta en proceso. Espere a que termine."

# ---------------------------------------------------------------------------
# queue / status
# ---------------------------------------------------------------------------

QUEUE_POSITION_PROMPT = "Su consulta está en la cola."
QUEUE_POSITION_HELP = "El operador responde en el orden de llegada."
QUEUE_POSITION_EMPTY = "No hay consultas pendientes."
QUEUE_POSITION_REFRESH = "Actualizar posición"

# ---------------------------------------------------------------------------
# answers
# ---------------------------------------------------------------------------

ANSWER_LABEL = "Respuesta"
ANSWER_CITATIONS_LABEL = "Fuentes consultadas"
ANSWER_EXCERPT_LABEL = "Ver extracto de la página"
ANSWER_TIMEOUT_LABEL = "Tiempo límite alcanzado"
ANSWER_TIMEOUT_PROMPT = "Se superó el tiempo de espera. Pruebe de nuevo."
ANSWER_INFRA_ERROR_LABEL = "Error del servicio"
ANSWER_INFRA_ERROR_PROMPT = "El servicio no está disponible en este momento. Vuelva a intentarlo más tarde."

# ---------------------------------------------------------------------------
# answer feedback (thumbs rating)
# ---------------------------------------------------------------------------

FEEDBACK_LABEL = "¿Fue útil esta respuesta?"
FEEDBACK_THUMBS_UP = "Útil"
FEEDBACK_THUMBS_DOWN = "No útil"
FEEDBACK_DONE = "Gracias por su valoración."

# ---------------------------------------------------------------------------
# owner operations
# ---------------------------------------------------------------------------

OWNER_TAB_TITLE = "Operaciones del propietario"
OWNER_UPLOAD_LABEL = "Subir manual actual (PDF)"
OWNER_UPLOAD_HELP = "Solo archivos PDF. El sistema los copia con nombre aleatorio."
OWNER_UPLOAD_EMPTY = "Seleccione un archivo PDF antes de subir."
OWNER_UPLOAD_BAD_MIME = "Solo se aceptan archivos PDF."
OWNER_UPLOAD_BAD_EXT = "Solo se aceptan archivos .pdf."
OWNER_UPLOAD_IN_PROGRESS = "Subiendo manual…"
OWNER_UPLOAD_DONE = "Manual subido correctamente."
OWNER_UPLOAD_FAILED = "No se pudo subir el manual. Revise el archivo e intente de nuevo."

OWNER_REINDEX_LABEL = "Reindexar manual actual"
OWNER_REINDEX_HELP = "Construye una nueva versión del índice y la pone en servicio."
OWNER_REINDEX_IN_PROGRESS = "Reindexando… Espere, la búsqueda se actualiza en segundo plano."
OWNER_REINDEX_DONE = "Reindexación completada."
OWNER_REINDEX_FAILED = "La reindexación falló sin mezclar revisiones. Sigue sirviendo la versión anterior."

OWNER_CODES_LABEL = "Gestionar códigos de operador"
OWNER_CODES_HELP = "Cada operador accede con su propio código y contraseña."
OWNER_CODE_NEW_LABEL = "Nuevo código"
OWNER_CODE_NEW_PASSWORD_LABEL = "Contraseña del nuevo código"
OWNER_CODE_ADD = "Agregar operador"
OWNER_CODE_ADD_DONE = "Operador agregado."
OWNER_CODE_ADD_DUPLICATE = "Ya existe un operador con ese código."
OWNER_CODE_REMOVE = "Eliminar operador"
OWNER_CODE_REMOVE_CONFIRM = "Eliminar a este operador?"
OWNER_CODE_REMOVE_DONE = "Operador eliminado."
OWNER_CODE_REMOVE_MISSING = "No existe ese operador."
OWNER_CODE_OWNER_CANNOT_REMOVE = "No se puede eliminar al último propietario registrado."

OWNER_CURRENT_USER = "Operador conectado"
OWNER_IS_OWNER = "Propietario"
OWNER_IS_OPERATOR = "Operador"

OWNER_SWITCH_ACCOUNT = "Cambiar cuenta"
OWNER_LOGOUT = "Salir"

# ---------------------------------------------------------------------------
# generic status
# ---------------------------------------------------------------------------

STATUS_OK = "Listo"
STATUS_ERROR = "Error"
STATUS_WAIT = "Cargando…"
