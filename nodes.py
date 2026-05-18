"""
nodes.py — Implementación de los 6 nodos del grafo ONTOMIND
Cada nodo recibe el estado, procesa y devuelve el estado actualizado.
"""
import os
import json
import asyncio
import time
import html as _html
from typing import TypedDict, Optional

import httpx

from prompts import (PROMPT_E_ACTOS, PROMPT_E_JUICIOS, PROMPT_P_QUIEBRE,
                     PROMPT_P_VICTIMA, PROMPT_DISTINCIONES, PROMPT_MAESTRO,
                     CONTEXTO_RAIZ_ANTROPOLOGICA, PROMPT_EVALUADOR,
                     PROMPT_EVALUADOR_CONVERSACION, CONTEXTO_ETICO_FUNDACIONAL,
                     DOCUMENTO_REFERENCIA_MAESTRO, FEW_SHOTS, seleccionar_few_shots)
from rag import recuperar_contexto, formatear_contexto
from memory import mapa_observador, sesion_redis

OPENAI_API_KEY    = "".join(os.getenv("OPENAI_API_KEY", "").split())
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
QDRANT_URL        = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY    = os.getenv("QDRANT_API_KEY", "").strip()
OPENAI_MODEL      = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# RunPod Serverless — modelo fine-tuneado ONTOMIND
RUNPOD_API_KEY    = os.getenv("RUNPOD_API_KEY", "").strip()
RUNPOD_ENDPOINT   = os.getenv("RUNPOD_ENDPOINT", "").strip()  # swon926uwdgq31
USAR_RUNPOD       = bool(RUNPOD_API_KEY and RUNPOD_ENDPOINT and os.getenv("USAR_RUNPOD_OVERRIDE","true").lower() == "true")
print(f"[RUNPOD] API_KEY={bool(RUNPOD_API_KEY)} ENDPOINT={RUNPOD_ENDPOINT} USAR={USAR_RUNPOD}")

RESPUESTA_ANCORA = """Lo que describes me importa, y quiero estar presente en esto contigo.

Hay momentos en que el peso de todo se vuelve demasiado grande para cargarlo solo. \
Eso no es una debilidad — es una señal de que necesitas apoyo real, no solo palabras.

¿Estarías dispuesto a hablar con alguien que pueda acompañarte de verdad en este momento?

🇪🇸 España: Teléfono de la Esperanza — 717 003 717
🇦🇷 Argentina: Centro de Asistencia al Suicida — 135
🇲🇽 México: SAPTEL — 55 5259-8121

Sigo aquí contigo."""

# ─── Estado del grafo ─────────────────────────────────────
class OntomindState(TypedDict):
    session_id:              str
    user_input:              str
    turno_actual:            int
    protocolo:               str        # normal|silencio|incoherencia|vigil
    reporte_actos:           dict
    reporte_juicios:         dict
    reporte_quiebre:         dict
    reporte_victima:         dict
    dictamen:                dict
    historial:               dict
    delta_observador:        str        # transformacion|regresion|estable
    confianza_victima_acum:  float
    pregunta_dominio_hecha:  bool
    nivel_riesgo:            str        # ninguno|latente|alto|critico
    umbral_vigil:            float
    en_resguardo:            bool
    ancora_previo:           bool
    respuesta:               str
    evaluacion:              dict       # metricas de recompensa antropologica
    evaluacion_conversacion: dict       # score 0-100 eje de transformacion
    user_code:               str        # codigo de usuario persistente
    turnos_sin_declaracion:  int        # contador para modo presencia
    mensajes_historial:      list       # historial de mensajes de la sesión


# ─── Llamada LLM genérica ────────────────────────────────
async def llamar_llm_runpod(system: str, user: str,
                            temperatura: float = 0.75,
                            max_tokens: int = 280,
                            messages: list = None) -> str:
    """Llamada al modelo fine-tuneado ONTOMIND via RunPod Serverless con polling.
    Si se pasa messages, se usan directamente en vez de construir desde system+user."""
    import asyncio
    modelo = os.getenv("OLLAMA_MODEL_NAME",
        "hf.co/Buyy/ontomind-qwen-14b/ontomind-qwen-14b-q4.gguf")

    if messages is None:
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user}
        ]

    headers = {
        "Authorization": f"Bearer {RUNPOD_API_KEY}",
        "Content-Type":  "application/json"
    }
    payload = {
        "input": {
            "model": modelo,
            "messages": messages,
            "options": {
                "temperature": temperatura,
                "num_predict": max_tokens,
            },
            "stream": False
        }
    }

    async with httpx.AsyncClient(timeout=300) as client:
        # Paso 1: enviar job con /run
        r = await client.post(
            f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT}/run",
            headers=headers,
            json=payload
        )
        r.raise_for_status()
        job_data = r.json()
        job_id = job_data.get("id")
        print(f"[RUNPOD] Job enviado: {job_id} | status: {job_data.get('status')}")

        if not job_id:
            print(f"[RUNPOD] Sin job_id: {job_data}")
            return ""

        # Paso 2: polling hasta completar
        for intento in range(150):  # máximo 5 minutos (150 * 2s)
            await asyncio.sleep(2)
            sr = await client.get(
                f"https://api.runpod.ai/v2/{RUNPOD_ENDPOINT}/status/{job_id}",
                headers=headers
            )
            sr.raise_for_status()
            status_data = sr.json()
            status = status_data.get("status", "")
            print(f"[RUNPOD] Polling {intento+1}: {status}")

            if status == "COMPLETED":
                output = status_data.get("output", {})
                print(f"[RUNPOD] Output completo: {str(output)[:200]}")
                # Formato lista: [{"choices": [{"message": {"content": "..."}}]}]
                if isinstance(output, list) and output:
                    item = output[0]
                    if isinstance(item, dict):
                        choices = item.get("choices", [])
                        if choices and isinstance(choices[0], dict):
                            msg = choices[0].get("message", {})
                            if isinstance(msg, dict):
                                return msg.get("content", "").strip()
                        # fallback dict plano
                        return item.get("response", item.get("content", "")).strip()
                # Formato dict plano
                if isinstance(output, dict):
                    choices = output.get("choices", [])
                    if choices and isinstance(choices[0], dict):
                        msg = choices[0].get("message", {})
                        if isinstance(msg, dict):
                            return msg.get("content", "").strip()
                    msg = output.get("message", {})
                    if isinstance(msg, dict):
                        return msg.get("content", "").strip()
                    return output.get("response", output.get("content", "")).strip()
                if isinstance(output, str):
                    return output.strip()
                return ""

            elif status in ("FAILED", "CANCELLED", "TIMED_OUT"):
                print(f"[RUNPOD] Job fallido: {status} | {status_data.get('error','')}")
                return ""

        print("[RUNPOD] Timeout de polling — 5 minutos sin respuesta")
        return ""

async def llamar_llm(system: str, user: str,
                     temperatura: float = 0.3,
                     es_maestro: bool = False,
                     forzar_openai: bool = False) -> str:
    """
    Llamada al LLM.
    - forzar_openai=True → siempre GPT-4o-mini (detectores, encuentro, evaluadores)
    - forzar_openai=False + USAR_RUNPOD → modelo fine-tuneado vía RunPod
    - Fallback → GPT-4o-mini
    """
    # RunPod Serverless — solo si no se fuerza OpenAI
    if USAR_RUNPOD and not forzar_openai:
        t = 0.45 if es_maestro else temperatura
        mx = 200 if es_maestro else 800
        return await llamar_llm_runpod(system, user, temperatura=t, max_tokens=mx)

    # Fallback: GPT-4o-mini
    if es_maestro:
        params = {
            "model":             OPENAI_MODEL,
            "temperature":       0.75,
            "top_p":             0.9,
            "frequency_penalty": 0.25,
            "presence_penalty":  0.3,
            "max_tokens":        280,
        }
    else:
        params = {
            "model":       OPENAI_MODEL,
            "temperature": temperatura,
            "max_tokens":  800,
        }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + OPENAI_API_KEY.strip().replace(chr(10),"").replace(chr(13),""),
                "Content-Type":  "application/json"
            },
            json={
                **params,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user}
                ]
            }
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


async def llamar_claude(system: str, user: str,
                        temperatura: float = 0.7,
                        max_tokens: int = 500) -> str:
    """
    Llamada a Claude (Anthropic API) para encuentro/escucha.
    Claude sigue instrucciones complejas de estilo mejor que GPT-4o-mini y Qwen.
    """
    if not ANTHROPIC_API_KEY:
        print("[CLAUDE] API key no configurada — fallback a GPT-4o-mini")
        return await llamar_llm(system, user, temperatura=temperatura, forzar_openai=True)

    async with httpx.AsyncClient(timeout=90) as client:
        try:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": max_tokens,
                    "temperature": temperatura,
                    "system": system,
                    "messages": [
                        {"role": "user", "content": user}
                    ]
                }
            )
            r.raise_for_status()
            data = r.json()
            # Claude devuelve content como lista de bloques
            content = data.get("content", [])
            if content and isinstance(content, list):
                return content[0].get("text", "").strip()
            return ""
        except Exception as e:
            print(f"[CLAUDE] Error: {e} — fallback a GPT-4o-mini")
            return await llamar_llm(system, user, temperatura=temperatura, forzar_openai=True)


async def llamar_llm_con_shots(system: str, user: str,
                                few_shots: list = None,
                                perfil: str = "") -> str:
    """
    Variante de llamar_llm para el nodo Maestro en fase INTERVENCIÓN.
    Usa RunPod (modelo fine-tuneado) cuando está disponible.
    Inyecta few-shots como mensajes de rol antes del input real.

    Para dolor_agudo aplica assistant prefill ("—") que fuerza
    la apertura con raya tipográfica, eliminando aperturas empáticas
    genéricas ("Entiendo que...", "Parece que...").
    """
    # Mapa de prefill por perfil
    PREFILL_POR_PERFIL = {
        "dolor_agudo":   "—",
        "juez_control":  "—",
        "mixto":         "—",
        "victima":       "—",
        "protagonista":  "—",
        "reflexivo":     "—",
    }

    messages = [{"role": "system", "content": system}]

    # Few-shots: el modelo fine-tuneado sí los aprovecha (fueron parte del DPO)
    if USAR_RUNPOD and few_shots:
        for u_shot, a_shot in few_shots:
            messages.append({"role": "user",      "content": u_shot})
            messages.append({"role": "assistant", "content": a_shot})

    # Input real del usuario
    messages.append({"role": "user", "content": user})

    # PREFILL: turno assistant parcial
    prefill_token = PREFILL_POR_PERFIL.get(perfil, "")
    if prefill_token:
        messages.append({"role": "assistant", "content": prefill_token})

    # ── RunPod: modelo fine-tuneado para intervención ──
    if USAR_RUNPOD:
        # Instrucciones negativas para Qwen v2
        refuerzo_qwen = (
            "\n\n━━━ RESTRICCIONES ABSOLUTAS ━━━\n"
            "PROHIBIDO inventar hechos sobre el usuario (padres, pareja, trabajo, "
            "colegas) que NO haya mencionado explícitamente.\n"
            "PROHIBIDO corregir el lenguaje del usuario — escuchar primero.\n"
            "OBLIGATORIO: UNA SOLA pregunta por respuesta.\n"
            "OBLIGATORIO: Máximo 2-3 frases.\n"
            "OBLIGATORIO: Primera palabra siempre raya tipográfica (—).\n"
            "\n━━━ TUTEO OBLIGATORIO ━━━\n"
            "Siempre de TÚ, nunca de USTED. "
            "PROHIBIDO: 'menciona', 'comenta', 'indica', 'refiere'. "
            "CORRECTO: 'dices', 'cuentas', 'sientes', 'quieres'. "
            "Ejemplo incorrecto: 'Eso que menciona sobre sentir...'\n"
            "Ejemplo correcto: 'Dices que estás mejor en tu soledad.'\n"
            "\n━━━ PALABRAS EXACTAS ━━━\n"
            "USA las palabras EXACTAS del usuario, no parafrasees. "
            "Si el usuario dijo 'mejor en mi soledad', tu respuesta debe "
            "contener 'mejor en tu soledad', no 'que prefieres estar solo'.\n"
            "\n━━━ VALIDACIÓN PROHIBIDA ━━━\n"
            "PROHIBIDO: 'suena profundo', 'suena difícil', 'suena duro', "
            "'eso es muy fuerte', 'qué importante', 'qué valiente'. "
            "NO evalúes lo que dice el usuario. Devuélvelo y pregunta.\n"
            "\n━━━ PREGUNTAS PROHIBIDAS ━━━\n"
            "PROHIBIDO preguntas genéricas de terapia: "
            "'cómo te hace sentir', 'qué sientes cuando', "
            "'cómo te sientes al respecto', 'cómo te sientes con eso'. "
            "En su lugar, pregunta algo ESPECÍFICO sobre lo que el usuario dijo. "
            "Incorrecto: '¿cómo te hace sentir cuando piensas en relacionarte?'\n"
            "Correcto: '¿Desde cuándo decidiste que los demás son un peligro?'\n"
            "\n━━━ SIN ENVOLTORIO ━━━\n"
            "PROHIBIDO empezar con 'Eso que dices de', 'Lo que dices sobre', "
            "'Lo que cuentas de'. Ve DIRECTO a las palabras del usuario.\n"
            "Incorrecto: '—Eso que dices de que piensas que estás mejor en tu soledad...'\n"
            "Correcto: '—Mejor en tu soledad. ¿Desde cuándo decidiste que estar con otros era peligroso?'"
        )
        # Inyectar refuerzo en el system message
        if messages and messages[0]["role"] == "system":
            messages[0]["content"] += refuerzo_qwen

        respuesta_raw = await llamar_llm_runpod(
            "", "", temperatura=0.45, max_tokens=200, messages=messages
        )
        if prefill_token and respuesta_raw and not respuesta_raw.startswith(prefill_token):
            return prefill_token + respuesta_raw
        return respuesta_raw if respuesta_raw else ""

    # ── Fallback: GPT-4o-mini ──
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + OPENAI_API_KEY.strip().replace(chr(10),"").replace(chr(13),""),
                "Content-Type":  "application/json"
            },
            json={
                "model":             OPENAI_MODEL,
                "temperature":       0.75,
                "top_p":             0.9,
                "frequency_penalty": 0.25,
                "presence_penalty":  0.3,
                "max_tokens":        280,
                "messages":          messages,
            }
        )
        r.raise_for_status()
        respuesta_raw = r.json()["choices"][0]["message"]["content"].strip()

    if prefill_token:
        return prefill_token + respuesta_raw
    return respuesta_raw


import re as _re

# Aperturas prohibidas que GPT-4o-mini y Qwen generan
_APERTURAS_PROHIBIDAS = [
    r"^Entiendo\b",
    r"^Comprendo\b",
    r"^Parece que\b",
    r"^Es comprensible\b",
    r"^Eso debe ser\b",
    r"^Puedo ver que\b",
    r"^Es normal\b",
    r"^Me imagino\b",
    r"^Qué difícil\b",
    r"^Veo que\b",
    r"^Siento que\b",
    r"^Te escucho\b",
    r"^Es válido\b",
    r"^Lo que sientes\b",
    r"^Lo que describes\b",
    r"^Es interesante\b",
    r"^Es curioso\b",
    r"^Costándote\b",
    r"^Llama la atención\b",
]
_PATRON_PROHIBIDAS = _re.compile("|".join(_APERTURAS_PROHIBIDAS), _re.IGNORECASE)

# Validación genérica y terapia-speak que ambos modelos generan
_VALIDACION_GENERICA = [
    r"suena bastante profundo",
    r"suena profundo",
    r"suena difícil",
    r"suena duro",
    r"suena muy fuerte",
    r"eso es muy fuerte",
    r"eso es muy valiente",
    r"qué importante",
    r"qué valiente",
    r"es muy valioso",
    r"es interesante cómo",
    r"es interesante que",
    r"es curioso que",
    r"es significativo",
    r"llama la atención que",
    r"lo que sugiere",
    r"eso podría indicar",
    r"podría haber algo más profundo",
    r"algo más profundo detrás",
    r"la soledad puede ser un refugio",
    r"a veces las cosas no son",
]
_PATRON_VALIDACION = _re.compile("|".join(_VALIDACION_GENERICA), _re.IGNORECASE)

# Correcciones de usted → tú (con todas las conjugaciones)
_USTED_A_TU = [
    (r"\bmenciona[s]?\b", "dices"),
    (r"\bmencionas\b", "dices"),
    (r"\bmencionando\b", "diciendo"),
    (r"\bcomenta[s]?\b", "cuentas"),
    (r"\bindica[s]?\b", "dices"),
    (r"\brefiere[s]?\b", "dices"),
    (r"\bEso que menciona[s]?\b", "Eso que dices"),
    (r"\blo que menciona[s]?\b", "lo que dices"),
    (r"\blo que comenta[s]?\b", "lo que cuentas"),
    (r"\bEso que mencionas de\b", "Dices que"),
]

# Frases genéricas de terapia que no aportan
_FRASES_TERAPIA = [
    r"cómo te hace sentir",
    r"como te hace sentir",
    r"qué sientes cuando",
    r"qué te hace sentir",
    r"cómo te sientes al respecto",
    r"cómo te sientes con eso",
]
_PATRON_TERAPIA = _re.compile("|".join(_FRASES_TERAPIA), _re.IGNORECASE)


# ─── Rate Limiter ─────────────────────────────────────────────
# Máximo MAX_MSG mensajes por sesión en VENTANA segundos
_RATE_STORE: dict = {}   # session_id → [timestamps]
_RATE_MAX_MSG   = int(os.getenv("RATE_MAX_MSG",   "30"))   # 30 mensajes
_RATE_VENTANA   = int(os.getenv("RATE_VENTANA",   "3600")) # por hora
_RATE_MAX_TURNO = int(os.getenv("RATE_MAX_TURNO", "40"))   # tope absoluto por sesión

def rate_check(session_id: str, turno_actual: int) -> bool:
    """Devuelve True si la petición está PERMITIDA. False si excede el límite."""
    # Tope absoluto de turnos por sesión
    if turno_actual > _RATE_MAX_TURNO:
        return False
    now = time.time()
    ventana_inicio = now - _RATE_VENTANA
    historial = _RATE_STORE.get(session_id, [])
    historial = [t for t in historial if t > ventana_inicio]
    if len(historial) >= _RATE_MAX_MSG:
        _RATE_STORE[session_id] = historial
        return False
    historial.append(now)
    _RATE_STORE[session_id] = historial
    return True


# ─── Blacklist de términos prohibidos ─────────────────────────
# Protege IP, privacidad y coherencia de marca.
# Sustituye en la SALIDA del modelo (no en el input del usuario).
_BLACKLIST_SUSTITUCION = [
    # Nombres propios — autores y personajes del cómic Pinotti
    (r'Pinotti',         'el coach',          True),
    (r'Nicolás',         'él',                True),
    (r'Nico',            'él',                True),
    (r'Mogilevsky',      '',                  True),
    (r'Herrera',         '',                  True),
    (r'Viacava',         '',                  True),
    # Filósofos, teóricos y autores — ninguno se nombra, sin excepción
    (r'\x08Echeverría\x08',  '',  True),
    (r'\x08Echeverria\x08',  '',  True),
    (r'\x08Flores\x08',  '',  True),
    (r'\x08Heidegger\x08',  '',  True),
    (r'\x08Sartre\x08',  '',  True),
    (r'\x08Frankl\x08',  '',  True),
    (r'\x08Kahneman\x08',  '',  True),
    (r'\x08Bateson\x08',  '',  True),
    (r'\x08Wittgenstein\x08',  '',  True),
    (r'\x08Maturana\x08',  '',  True),
    (r'\x08Winograd\x08',  '',  True),
    (r'\x08Aristóteles\x08',  '',  True),
    (r'\x08Aristoteles\x08',  '',  True),
    (r'\x08Platón\x08',  '',  True),
    (r'\x08Platon\x08',  '',  True),
    (r'\x08Sócrates\x08',  '',  True),
    (r'\x08Socrates\x08',  '',  True),
    (r'\x08Nietzsche\x08',  '',  True),
    (r'\x08Foucault\x08',  '',  True),
    (r'\x08Descartes\x08',  '',  True),
    (r'\x08Kant\x08',  '',  True),
    (r'\x08Spinoza\x08',  '',  True),
    (r'\x08Derrida\x08',  '',  True),
    (r'\x08Lacan\x08',  '',  True),
    (r'\x08Freud\x08',  '',  True),
    (r'\x08Jung\x08',  '',  True),
    (r'\x08Buber\x08',  '',  True),
    (r'\x08Arendt\x08',  '',  True),
    (r'\x08Camus\x08',  '',  True),
    (r'\x08Merleau-Ponty\x08',  '',  True),
    (r'\x08Habermas\x08',  '',  True),
    (r'\x08Austin\x08',  '',  True),
    (r'\x08Searle\x08',  '',  True),
    # Terminología académica → lenguaje cotidiano
    (r'ontología',       'coaching',          True),
    (r'ontológico',      'de crecimiento personal', True),
    (r'ontológica',      'de crecimiento personal', True),
    (r'ontológicos',     'de crecimiento personal', True),
    (r'ontológicas',     'de crecimiento personal', True),
    # Editorial
    (r'Coaching Ediciones', '', True),
]

def aplicar_blacklist(texto: str) -> str:
    """Elimina o sustituye términos prohibidos en la salida del modelo."""
    import re as _re2
    for patron, reemplazo, ignorar_case in _BLACKLIST_SUSTITUCION:
        flags = _re2.IGNORECASE if ignorar_case else 0
        texto = _re2.sub(patron, reemplazo, texto, flags=flags)
    # Limpiar dobles espacios que puedan quedar tras eliminar palabras
    texto = _re2.sub(r'  +', ' ', texto).strip()
    return texto


# ─── Sanitizador de input del usuario ─────────────────────────
_PATRON_CODIGO = _re.compile(
    r'```.*?```|`[^`]+`'                         # bloques de código markdown
    r'|<[a-zA-Z][^>]{0,200}>.*?</[a-zA-Z]+>'     # etiquetas HTML
    r'|<[a-zA-Z][^>]{0,200}/?>',                 # etiquetas HTML autocerradas
    _re.DOTALL | _re.IGNORECASE
)
_PATRON_SCRIPTS  = _re.compile(r'<script[\s\S]*?</script>', _re.IGNORECASE)
_PATRON_LINKS    = _re.compile(r'https?://\S+')

def sanitizar_input(texto: str) -> str:
    """
    Limpia el input del usuario:
    - Elimina bloques de código (```, <code>, <script>)
    - Elimina etiquetas HTML
    - Elimina URLs
    - Decodifica entidades HTML
    - Trunca a 2000 caracteres
    Devuelve texto plano seguro.
    """
    texto = _PATRON_SCRIPTS.sub('', texto)
    texto = _PATRON_CODIGO.sub('', texto)
    texto = _PATRON_LINKS.sub('[enlace eliminado]', texto)
    texto = _html.unescape(texto)           # &amp; → &, etc.
    texto = _re.sub(r'[ \t]+', ' ', texto)  # espacios múltiples
    texto = texto.strip()
    # Truncar si es demasiado largo
    if len(texto) > 2000:
        texto = texto[:2000] + '…'
    return texto


def limpiar_respuesta_gpt(texto: str, user_input: str = "") -> str:
    """
    Post-procesado mecánico agresivo.
    Filtra frases completas con patrones de terapia-speak.
    """
    texto = texto.strip()
    if not texto:
        return texto

    # Paso 0: corregir usted → tú
    for patron, reemplazo in _USTED_A_TU:
        texto = _re.sub(patron, reemplazo, texto, flags=_re.IGNORECASE)

    # Paso 0a: eliminar envoltorios innecesarios
    _envoltorios = [
        r"^—?Eso que dices de que ",
        r"^—?Eso que dices de ",
        r"^—?Eso que dices sobre ",
        r"^—?Lo que dices de que ",
        r"^—?Lo que dices de ",
        r"^—?Lo que dices sobre ",
        r"^—?Lo que cuentas de que ",
        r"^—?Lo que cuentas de ",
        r"^—?Lo que cuentas sobre ",
        r"^—?Te sigue costando ",
        r"^—?Me sigue costando ",
        r"^—?Costándote ",
        r"^—?Te cuesta ",
    ]
    for env in _envoltorios:
        if _re.match(env, texto, _re.IGNORECASE):
            texto = _re.sub(env, "—", texto, count=1, flags=_re.IGNORECASE)
            if len(texto) > 1 and texto[0] == "—":
                texto = "—" + texto[1].upper() + texto[2:]
            break

    # Paso 1: FILTRADO LIGERO — solo los patrones más tóxicos
    # Paso 1: Reemplazos suaves (no rompen gramática)
    # Solo eliminar "Esa repetición" como frase introductoria si aparece
    texto = _re.sub(r"\. Esa repetición[^.]*\.", ".", texto)

    # Paso 2: eliminar apertura prohibida (puede haber quedado tras filtrado)
    texto_sin_raya = texto.lstrip("—").strip()
    match = _PATRON_PROHIBIDAS.match(texto_sin_raya)
    if match:
        primera_frase_end = None
        for sep in [". ", ".\n"]:
            pos = texto_sin_raya.find(sep)
            if pos > 0:
                primera_frase_end = pos + 1
                break
        if primera_frase_end:
            texto_sin_raya = texto_sin_raya[primera_frase_end:].strip()
        else:
            texto_sin_raya = _PATRON_PROHIBIDAS.sub("", texto_sin_raya).strip()
        texto = texto_sin_raya

    # Paso 3: asegurar raya tipográfica al inicio
    if not texto.startswith("—"):
        texto = "—" + texto

    # Paso 4: limpiar espacios dobles y puntos dobles
    texto = _re.sub(r"  +", " ", texto)
    texto = _re.sub(r"\.\.", ".", texto)
    texto = texto.strip()

    # Paso 5: aplicar blacklist de términos prohibidos
    texto = aplicar_blacklist(texto)

    return texto


async def parsear_json(texto: str) -> dict:
    """Extrae JSON de la respuesta del LLM de forma robusta."""
    try:
        inicio = texto.find("{")
        fin    = texto.rfind("}") + 1
        if inicio >= 0 and fin > inicio:
            return json.loads(texto[inicio:fin])
    except Exception:
        pass
    return {}


# ─── NODO 0: Calibrar Escucha (Abrazo Invisible) ─────────
async def nodo_calibrar_escucha(state: OntomindState) -> OntomindState:
    """
    Lee el flag ANCORA del historial en Supabase.
    Ajusta el umbral de detección silenciosamente.
    El usuario no ve ningún cambio.
    """
    mapa = await mapa_observador.get(state["session_id"])
    ancora_previo       = mapa.get("ancora_activado", False)
    turnos_desde_ancora = mapa.get("turnos_desde_ancora", 999)
    en_resguardo        = ancora_previo and turnos_desde_ancora <= 5

    state["historial"]    = mapa
    state["ancora_previo"] = ancora_previo
    state["en_resguardo"]  = en_resguardo
    # 20% más sensible si está en periodo de resguardo
    state["umbral_vigil"]  = 0.56 if en_resguardo else 0.70

    # Cargar historial de mensajes de la sesión para contexto conversacional
    try:
        mensajes = await sesion_redis.get_mensajes(state["session_id"])
        state["mensajes_historial"] = mensajes[-10:]  # últimos 5 intercambios
    except Exception:
        state["mensajes_historial"] = []


    # ── Cargar mapa neural + resumen de sesión anterior ──
    user_code_cal = state.get("user_code", "anonimo")
    if user_code_cal != "anonimo" and state.get("turno_actual", 1) == 1:
        try:
            mapa = await cargar_mapa_neural(user_code_cal)
            if mapa:
                state["mapa_neural"] = mapa
                resumen_mapa = await resumir_mapa_neural(mapa)
                if resumen_mapa:
                    state["resumen_mapa_neural"] = resumen_mapa
                    print(f"[MAPA-NEURAL] Cargado para {user_code_cal}: {resumen_mapa[:80]}...")
        except Exception as e:
            print(f"[MAPA-NEURAL] Error carga: {e}")

        try:
            import httpx as _httpx_cal
            supa_url = os.getenv("SUPABASE_URL", "").strip()
            supa_key = os.getenv("SUPABASE_KEY", "").strip()
            if supa_url and supa_key:
                async with _httpx_cal.AsyncClient(timeout=8) as client_cal:
                    r = await client_cal.get(
                        f"{supa_url}/rest/v1/usuarios"
                        f"?user_code=eq.{user_code_cal}&select=ultimo_resumen&limit=1",
                        headers={"apikey": supa_key, "Authorization": f"Bearer {supa_key}"},
                    )
                if r.status_code == 200:
                    data_cal = r.json()
                    if data_cal and data_cal[0].get("ultimo_resumen"):
                        state["resumen_sesion_anterior"] = data_cal[0]["ultimo_resumen"]
                        print(f"[MEMORIA] Resumen cargado para {user_code_cal}")
        except Exception as e:
            print(f"[MEMORIA] Error cargando resumen: {e}")

    return state



# ─── MAPA NEURAL — Funciones de inferencia biográfica ─────────

async def cargar_mapa_neural(user_code: str) -> dict:
    """Carga el mapa neural desde Supabase. Devuelve dict vacío si no existe."""
    if not user_code or user_code == "anonimo":
        return {}
    try:
        import httpx as _hx
        supa_url = os.getenv("SUPABASE_URL", "").strip()
        supa_key = os.getenv("SUPABASE_KEY", "").strip()
        if not supa_url:
            return {}
        async with _hx.AsyncClient(timeout=8) as cl:
            r = await cl.get(
                f"{supa_url}/rest/v1/usuarios"
                f"?user_code=eq.{user_code}&select=mapa_neural&limit=1",
                headers={"apikey": supa_key, "Authorization": f"Bearer {supa_key}"},
            )
        if r.status_code == 200:
            data = r.json()
            if data and data[0].get("mapa_neural"):
                return data[0]["mapa_neural"] if isinstance(data[0]["mapa_neural"], dict) else {}
    except Exception as e:
        print(f"[MAPA-NEURAL] Error cargando: {e}")
    return {}


async def guardar_mapa_neural(user_code: str, mapa: dict):
    """Guarda el mapa neural en Supabase."""
    if not user_code or user_code == "anonimo":
        return
    try:
        import httpx as _hx
        supa_url = os.getenv("SUPABASE_URL", "").strip()
        supa_key = os.getenv("SUPABASE_KEY", "").strip()
        if not supa_url:
            return
        async with _hx.AsyncClient(timeout=8) as cl:
            await cl.post(
                f"{supa_url}/rest/v1/usuarios",
                headers={
                    "apikey": supa_key,
                    "Authorization": f"Bearer {supa_key}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                json={"user_code": user_code, "mapa_neural": mapa},
            )
        print(f"[MAPA-NEURAL] Guardado para {user_code}")
    except Exception as e:
        print(f"[MAPA-NEURAL] Error guardando: {e}")


async def actualizar_mapa_neural(state: dict) -> dict:
    """Infiere y actualiza el mapa neural desde la conversación actual."""
    from prompts import PROMPT_MAPA_NEURAL
    user_code = state.get("user_code", "anonimo")
    if user_code == "anonimo":
        return state.get("mapa_neural", {})

    mapa_previo = state.get("mapa_neural", {})
    mensajes = await sesion_redis.get_mensajes(state.get("session_id", ""))

    historial = ""
    for m in mensajes[-8:]:
        rol = "Usuario" if m.get("rol") == "user" else "ONTOMIND"
        historial += f"{rol}: {m.get('contenido', '')[:250]}\n"

    if not historial.strip():
        return mapa_previo

    import json as _json_map
    mapa_str = _json_map.dumps(mapa_previo, ensure_ascii=False) if mapa_previo else "{}"

    prompt = PROMPT_MAPA_NEURAL.format(
        historial=historial.strip(),
        mapa_previo=mapa_str,
    )

    try:
        respuesta = await llamar_llm(
            prompt, "", temperatura=0.3, forzar_openai=True
        )
        respuesta = _re.sub(r"^```json\s*", "", respuesta.strip())
        respuesta = _re.sub(r"\s*```$", "", respuesta)
        nuevo_mapa = _json_map.loads(respuesta)

        # Merge: mantener valores previos donde el nuevo pone -1
        if mapa_previo.get("ejes") and nuevo_mapa.get("ejes"):
            for eje, val in nuevo_mapa["ejes"].items():
                if val == -1 and eje in mapa_previo["ejes"]:
                    nuevo_mapa["ejes"][eje] = mapa_previo["ejes"][eje]

        await guardar_mapa_neural(user_code, nuevo_mapa)
        print(f"[MAPA-NEURAL] Actualizado — confianza: {nuevo_mapa.get('confianza_global', '?')}")
        return nuevo_mapa

    except Exception as e:
        print(f"[MAPA-NEURAL] Error inferencia: {e}")
        return mapa_previo


async def resumir_mapa_neural(mapa: dict) -> str:
    """Genera un resumen de ~120 palabras del mapa para inyectar en el Maestro."""
    if not mapa or not mapa.get("ejes"):
        return ""
    from prompts import PROMPT_RESUMEN_MAPA
    import json as _json_res
    try:
        resumen = await llamar_llm(
            PROMPT_RESUMEN_MAPA.format(mapa_json=_json_res.dumps(mapa, ensure_ascii=False)),
            "", temperatura=0.3, forzar_openai=True
        )
        return resumen.strip()
    except Exception as e:
        print(f"[MAPA-NEURAL] Error resumen: {e}")
        return ""


# ─── NODO 1: Clasificar Input ────────────────────────────
async def nodo_clasificar_input(state: OntomindState) -> OntomindState:
    """Detecta si el input es silencio/mínimo o normal."""
    # Sanitizar y verificar rate limit
    texto_raw = sanitizar_input(state.get("user_input", ""))
    state["user_input"] = texto_raw

    if not rate_check(state.get("session_id", ""), state.get("turno_actual", 1)):
        state["respuesta"] = "—He notado que llevamos mucho tiempo juntos en esta sesión. Tómate un respiro — aquí seguimos cuando quieras volver."
        state["protocolo"] = "normal"
        return state

    texto  = texto_raw.strip()
    tokens = texto.split()

    tokens_silencio = {"no sé", "no se", "no lo sé", "quizás", "tal vez",
                       "no", "nada", "da igual", "...", "no sé qué decir"}

    # Silencio SOLO si el input es mínimo Y no contiene contenido emocional real
    # "Me siento fatal hoy" NO es silencio — es una declaración emocional de 4 palabras
    tiene_contenido_emocional = any(w in texto.lower() for w in [
        "siento", "fatal", "mal", "bien", "miedo", "dolor", "triste",
        "solo", "sola", "perdido", "perdida", "cansado", "cansada",
        "culpa", "vergüenza", "rabia", "enfado", "llorar", "lloro"
    ])

    # Detectar saludos puros y preguntas sobre identidad
    texto_lower = texto.lower().strip().rstrip("!?.,")
    SALUDOS = {
        "hola", "buenos días", "buenas tardes", "buenas noches", "buenas",
        "hey", "hi", "hello", "holi", "qué tal", "que tal", "cómo estás",
        "como estás", "como estas", "ey", "buenas noches", "buenas dias"
    }
    # Señales de identidad — el usuario no sabe qué es esto o busca orientación
    PALABRAS_IDENTIDAD = [
        # Preguntas sobre identidad
        "quien eres", "quién eres", "que eres", "qué eres",
        "eres una ia", "eres un bot", "eres humano", "eres una inteligencia",
        "eres una especie", "eres como", "eres un",
        # Preguntas sobre función
        "para que", "para qué", "para que sirve", "para qué sirve",
        "que puedo encontrar", "qué puedo encontrar",
        "que puedo hacer", "qué puedo hacer",
        "que se hace", "qué se hace",
        "que ofreces", "qué ofreces", "que puedes ofrecerme", "qué puedes ofrecerme",
        "cómo funciona", "como funciona", "cómo se usa", "como se usa",
        # No saber por dónde empezar
        "no sé por donde", "no se por donde",
        "no sé cómo empezar", "no se como empezar",
        "no sé qué hacer", "no se que hacer",
        "no sé muy bien", "no se muy bien",
        "por donde empiezo", "por dónde empiezo",
        "iniciarme", "iniciame", "empieza tú", "empieza tu",
        # Confusión directa
        "no entiendo", "no acabo de entender", "no lo entiendo",
        "explícame", "explicame", "qué es esto", "que es esto",
        "este chat", "esta app", "este lugar", "en este sitio",
        # Preguntas sobre valor
        "de que me valdra", "de qué me valdrá", "para qué me sirve",
        "para que me sirve", "que gano", "qué gano",
        "que obtendre", "qué obtendré", "que obtengo", "qué obtengo",
    ]

    # Saludo puro — texto muy corto con palabras de saludo
    es_saludo = texto_lower in SALUDOS or (
        len(tokens) <= 4 and any(s in texto_lower for s in ["hola", "hey", "buenas", "hi", "buenas tardes", "buenos días"])
    )

    # Pregunta de identidad — señales ambiguas solo activan en mensajes cortos (<150 chars)
    _texto_corto = len(texto) < 150
    _IDENTIDAD_SOLO_CORTO = {
        "que obtengo", "qué obtengo", "que obtendre", "qué obtendré",
        "que gano", "qué gano", "de que me valdra", "de qué me valdrá",
        "para que me sirve", "para qué me sirve",
        "que puedo encontrar", "qué puedo encontrar",
        "que puedo hacer", "qué puedo hacer",
        "que se hace", "qué se hace",
    }
    es_pregunta_identidad = any(
        p in texto_lower
        for p in PALABRAS_IDENTIDAD
        if p not in _IDENTIDAD_SOLO_CORTO or _texto_corto
    )

    # Si el primer turno tiene saludo + pregunta de identidad → identidad tiene prioridad
    # Mantener protocolo apertura durante los primeros 3 turnos si hay confusión
    turno_actual    = state.get("turno_actual", 1)
    # identidad solo en los 2 primeros turnos
    es_primer_turno = turno_actual <= 2

    # Lenguaje de muerte o colapso psicológico → siempre Claude, siempre nombrar
    DOLOR_AGUDO_LEXICON = [
        "tumba", "me mata", "me destruye", "me aplasta", "me ahoga",
        "no puedo más", "al límite", "derrumbado", "hundido", "roto",
        "vacío total", "sin salida", "agotado de verdad", "perdido del todo",
        "no quiero seguir", "no tiene sentido mi vida", "para qué vivir",
    ]
    es_dolor_agudo_profundo = any(p in texto_lower for p in DOLOR_AGUDO_LEXICON)
    if es_dolor_agudo_profundo:
        state["protocolo"] = "dolor_agudo"

    es_silencio = (
        (len(tokens) < 4 or texto.lower() in tokens_silencio or len(texto) < 10)
        and not tiene_contenido_emocional
        and not es_saludo
        and not es_pregunta_identidad
    )

    # ── Lead Magnet: si hay protocolo creencia activo, mantenerlo ──
    sesion_data = await sesion_redis.get(state.get("session_id", ""))
    lead_state  = sesion_data.get("lead_magnet", {})

    if lead_state.get("tipo") == "creencia" and lead_state.get("ronda", 0) < 5:
        # Válvula de escape: si el usuario empuja contra el protocolo, salir
        RECHAZO_CREENCIA = [
            "no entiendo", "no lo veo", "no sé por qué", "no sé porque",
            "me llevas a lo que no es", "no tiene sentido",
            "no estoy de acuerdo", "eso no es así", "por qué va a ser falsa",
            "porque va a ser falsa", "camino diferente", "no me convence",
            "esto no funciona", "no es una creencia", "es una certeza",
            "te estoy dando casos", "son hechos",
        ]
        if any(p in texto_lower for p in RECHAZO_CREENCIA):
            # El usuario rechaza el protocolo → volver a encuentro normal
            sesion_data["lead_magnet"] = {"tipo": "creencia_abandonada"}
            await sesion_redis.set(state.get("session_id", ""), sesion_data)
            state["protocolo"] = "normal"
            print("[LEAD] Creencia abandonada por rechazo del usuario")
        else:
            state["protocolo"] = "lead_creencia"
        return state

    # ── Detección de lead_oferta: mensaje vago/de prueba en turno 1 ──
    PALABRAS_VAGO = [
        "a ver", "vamos a ver", "probar", "probando", "solo quería probar",
        "solo queria probar", "a ver qué tal", "a ver que tal",
        "no sé qué poner", "no se que poner", "no tengo claro",
        "pues nada", "no sé", "no se", "a ver qué pasa", "voy a probar",
        "quiero probar", "quiero ver", "estoy curioseando",
    ]
    # Señales que BLOQUEAN lead_oferta — contenido real, no tanteo
    PERSONAS_MENCIONADAS = [
        "hermano", "hermana", "padre", "madre", "mamá", "papá",
        "pareja", "novio", "novia", "marido", "mujer", "esposa", "esposo",
        "hijo", "hija", "hijos", "jefe", "compañero", "amigo", "amiga",
        "familia", "ex ", "suegra", "suegro", "cuñado", "cuñada",
        "colega", "socio", "profesor", "vecino",
    ]
    EMOCIONES_DIRECTAS = [
        "me siento", "siento que", "me duele", "me frustra", "me angustia",
        "tengo miedo", "me da rabia", "estoy harto", "me agobia",
        "no puedo más", "me cuesta", "no sé cómo", "me preocupa",
        "discutiendo", "discutimos", "peleamos", "conflicto",
        "soledad", "solo", "sola", "triste", "ansioso", "ansiosa",
        "relación", "no hablamos", "no me habla", "no le hablo",
    ]
    tiene_persona    = any(p in texto_lower for p in PERSONAS_MENCIONADAS)
    tiene_emocion    = any(e in texto_lower for e in EMOCIONES_DIRECTAS)
    tiene_situacion  = tiene_persona or tiene_emocion or len(texto) > 100

    es_vago = (
        turno_actual <= 2
        and not es_pregunta_identidad
        and not tiene_contenido_emocional
        and not tiene_situacion
        and (
            any(p in texto_lower for p in PALABRAS_VAGO)
            or (len(texto) < 40 and not es_saludo and len(tokens) > 1)
        )
    )

    # ── Detección de elección de lead magnet tras oferta ──
    ELIGE_CREENCIA = [
        "creencia", "desmontar", "lo segundo", "la segunda", "segunda opción",
        "adelante con las preguntas", "las preguntas", "con las preguntas",
        "preguntas", "adelante", "empieza", "empezamos", "por las preguntas",
        "el segundo", "segunda", "desmonta", "quiero las preguntas",
        "hazme las preguntas", "haz las preguntas", "vamos con las preguntas",
    ]
    ELIGE_ESPEJO = [
        "espejo", "ceguera", "lo primero", "la primera", "primera opción",
        "descubrir", "el primero", "primera", "quiero descubrir",
        "descúbreme", "muéstrame", "la ceguera", "ver lo que no veo",
    ]

    es_respuesta_lead = lead_state.get("tipo") == "oferta_hecha"
    if es_respuesta_lead:
        if any(p in texto_lower for p in ELIGE_CREENCIA):
            state["protocolo"] = "lead_creencia"
            return state
        elif any(p in texto_lower for p in ELIGE_ESPEJO):
            state["protocolo"] = "lead_espejo"
            return state
        else:
            # El usuario ignora la oferta → flujo normal
            pass

    if (es_pregunta_identidad and es_primer_turno) or (es_primer_turno and es_saludo):
        # Identidad/saludo SOLO en turnos 1-2 — después, "no entiendo"
        # es frustración legítima, no confusión sobre el servicio
        state["protocolo"] = "identidad" if es_pregunta_identidad else "saludo"
    elif es_saludo:
        state["protocolo"] = "saludo"
    elif es_silencio:
        state["protocolo"] = "silencio"
    elif es_vago:
        state["protocolo"] = "lead_oferta"
    else:
        state["protocolo"] = "normal"
    return state


# ─── NODO 2: Detectores en paralelo ─────────────────────
async def _detector(nodo_id: str, prompt: str,
                    user_input: str, query_rag: str) -> dict:
    """Ejecuta un detector individual con RAG."""
    fragmentos = await recuperar_contexto(nodo_id, query_rag, top_k=3)
    contexto   = formatear_contexto(fragmentos)
    user_msg   = f"TEXTO DEL USUARIO:\n{user_input}\n\nCONTEXTO DEL CORPUS:\n{contexto}"
    respuesta  = await llamar_llm(prompt, user_msg, forzar_openai=True)
    return await parsear_json(respuesta)


async def nodo_detectores(state: OntomindState) -> OntomindState:
    """Ejecuta los 4 detectores en paralelo. Sin acceso al historial."""
    texto = state["user_input"]

    resultados = await asyncio.gather(
        _detector("e_actos",   PROMPT_E_ACTOS,
                  texto, "actos lingüísticos declaración promesa petición"),
        _detector("e_juicios", PROMPT_E_JUICIOS,
                  texto, "juicio afirmación juicio maestro verdad absoluta"),
        _detector("p_quiebre", PROMPT_P_QUIEBRE,
                  texto, "quiebre ontológico transparencia modelo OSAR identidad"),
        _detector("p_victima", PROMPT_P_VICTIMA,
                  texto, "víctima protagonista responsabilidad autoridad ontológica"),
        return_exceptions=True
    )

    state["reporte_actos"]   = resultados[0] if isinstance(resultados[0], dict) else {}
    state["reporte_juicios"] = resultados[1] if isinstance(resultados[1], dict) else {}
    state["reporte_quiebre"] = resultados[2] if isinstance(resultados[2], dict) else {}
    state["reporte_victima"] = resultados[3] if isinstance(resultados[3], dict) else {}
    return state


# ─── NODO 3: Detector de Incoherencia ────────────────────
async def nodo_incoherencia(state: OntomindState) -> OntomindState:
    """Detecta contradicción Promesa+Víctima simultáneos."""
    actos   = state["reporte_actos"]
    victima = state["reporte_victima"]

    hay_promesa  = actos.get("acto_dominante") == "PROMESA"
    hay_victima  = victima.get("posicion") == "victima"
    conf_actos   = float(actos.get("confianza", 0))
    conf_victima = float(victima.get("confianza", 0))

    if (hay_promesa and hay_victima and
            conf_actos > 0.70 and conf_victima > 0.70):
        state["protocolo"] = "incoherencia"

    return state


# ─── NODO 4: Triple Filtro VIGIL ─────────────────────────
async def nodo_triple_filtro_vigil(state: OntomindState) -> OntomindState:
    """Detecta riesgo existencial mediante 3 señales ontológicas."""
    texto   = state["user_input"].lower()
    tokens  = set(texto.split())
    nivel   = "ninguno"
    umbral  = state.get("umbral_vigil", 0.70)

    # Señal 1: Deserción del Protagonista (multi-turno)
    conf_victima = float(state["reporte_victima"].get("confianza", 0))
    acum = state.get("confianza_victima_acum", 0)
    acum = acum + 1 if conf_victima > 0.95 else 0
    state["confianza_victima_acum"] = acum
    if acum >= 3:
        nivel = "latente"

    # Señal 2: Temporalidad de Resignación vinculada al SER
    tokens_tiempo = {"nunca", "siempre", "jamás", "final", "para siempre", "ya no"}
    tokens_ser    = {"soy", "no soy", "no sirvo", "no valgo", "no puedo ser",
                     "no importo", "no existo", "estoy solo", "no me queda"}
    if tokens & tokens_tiempo and tokens & tokens_ser:
        nivel = "alto"

    # Señal 3: Colapso OSAR completo
    quiebre = state["reporte_quiebre"]
    if (quiebre.get("tipo_quiebre") == "ontologico" and
            quiebre.get("intensidad") == "profundo" and
            quiebre.get("osar_afectado") == "completo"):
        nivel = "alto"

    # HARD-LOCK v2.1 — Umbral recalibrado para reducir falsos positivos
    conf_victima_actual = float(state["reporte_victima"].get("confianza", 0))
    dominios_colapso = {"sentido", "vida", "multiple"}  # "identidad" EXCLUIDO — es coaching normal
    dominio_actual = quiebre.get("dominio_afectado", "")

    # Detectar Llave Maestra activa (si hay llave, es coaching no crisis)
    llave_activa = state.get("dictamen", {}).get("llave_maestra", "")
    hay_llave_maestra = bool(llave_activa)

    # Señales de daño inminente (self-harm) — umbral NUNCA baja
    tokens_daño_real = {
        "hacerme daño", "hacerme algo", "quitarme la vida", "suicidarme",
        "no quiero vivir", "mejor muerto", "mejor muerta", "acabar con todo",
        "atentar contra", "hacerle daño", "matarme", "matarle"
    }
    daño_inminente = any(t in texto for t in tokens_daño_real)

    # HARD-LOCK 1: Daño inminente = CRÍTICO siempre (sin excepción)
    if daño_inminente:
        nivel = "critico"

    # Tokens EXCLUIDOS de frustración — confirmados como falsos positivos:
    # son lenguaje de estancamiento laboral/relacional, NO de crisis.
    tokens_falso_positivo = {
        # Vocabulario ontológico/laboral
        "estoy atrapado", "me siento atrapado", "atrapado en",
        "asfixiando", "me está asfixiando", "cerrando el espacio",
        "validar mi identidad", "transformar mi escucha",
        "brecha de efectividad", "no-posibilidad", "llave maestra",
        # Frustración normal — NO crisis
        "impotente", "insignificante", "me siento pequeño",
        "transparente", "no me valoran", "no me ven",
        "espiral", "me traga", "me enoja", "me enfada",
        "no cuento para nadie", "nadie me escucha",
        # Frustración dirigida al sistema (no autolesión)
        "no vales para nada", "no sirves", "no me escuchas",
        "tú tampoco", "tampoco me escuchas", "no me entiendes",
        "tú no vales", "eres inútil",
        # Adolescentes — expresiones normales de desarrollo
        "independencia", "mi propia imagen", "tratar como un niño",
        "como a un niño", "no me dejan", "quiero ser",
        # Aspiracionales — respuestas positivas, NO son crisis
        "autonomía", "paz", "desapego", "autoconocimiento",
        "conexión con uno mismo", "conexión conmigo",
        "libertad", "sabiduría", "tranquilidad", "plenitud",
        "ermitaño", "soledad elegida", "mi camino",
        "ser yo mismo", "encontrarme", "crecer",
    }
    # FILTRO: cierres filosóficos/cognitivos nunca son crisis
    CIERRES_FILOSOFICOS = {
        "eso no va a ocurrir", "no va a ocurrir", "no ocurrirá",
        "no lo veo posible", "no creo en eso", "es imposible cambiar",
        "nunca cambiará", "da igual", "para qué",
        "no tiene sentido seguir", "no cambiará nada",
    }
    es_cierre_filosofico = any(p in texto for p in CIERRES_FILOSOFICOS)
    if es_cierre_filosofico:
        # Cierre cognitivo: máximo latente, nunca crítico ni alto
        if nivel in ("alto", "critico"):
            nivel = "latente"
            print("[VIGIL] Cierre filosófico — reducido a latente")

    # Si el mensaje contiene tokens de falso positivo, reducir nivel máximo a latente
    es_falso_positivo = any(t in texto for t in tokens_falso_positivo)

    # FILTRO FALSO POSITIVO: tokens de vocabulario ontológico/laboral
    # nunca superan nivel "latente" aunque haya otros indicadores
    if es_falso_positivo and not daño_inminente:
        if nivel in ("alto", "critico"):
            nivel = "latente"
            print(f"[VIGIL] Falso positivo detectado — nivel reducido a latente")

    # HARD-LOCK 2: Crisis existencial COMPLETA requiere 3 señales simultáneas
    # (dominio vital + víctima muy alta + sin llave maestra de coaching)
    elif dominio_actual in dominios_colapso and conf_victima_actual > 0.85 and not hay_llave_maestra:
        nivel = "critico"

    # HARD-LOCK 3: Señal aislada de frustración extrema → solo ALTO (no crítico)
    # VIGIL alerta al supervisor pero el Maestro continúa con Rotundidad Amorosa
    tokens_frustracion = {
        "no puedo mas", "no puedo más", "ya no puedo", "no tengo fuerzas",
        "sin salida", "no hay salida", "bucle sin salida", "le colgue", "le colgué"
    }
    tokens_rechazo_ayuda = {
        "no me preguntes", "dejar constancia", "no quiero ayuda", "nadie me ayude"
    }
    frustracion_aislada = any(t in texto for t in tokens_frustracion)
    rechazo_ayuda = any(t in texto for t in tokens_rechazo_ayuda)

    if frustracion_aislada and nivel == "ninguno":
        # Frustración sin otros indicadores → latente, no crítico
        nivel = "latente"
    if rechazo_ayuda and nivel in ("ninguno", "latente"):
        # Rechazo de ayuda sin señales de daño → alto, no crítico
        nivel = "alto"

    # FILTRO CONTEXTUAL: Si hay Llave Maestra de coaching (Dignidad, Justicia, etc.)
    # VIGIL cede prioridad al Maestro — es coaching, no crisis psiquiátrica
    if hay_llave_maestra and nivel == "critico" and not daño_inminente:
        nivel = "alto"
        print(f"[VIGIL] Cediendo prioridad al Maestro — Llave Maestra activa: {llave_activa}")

    # FAIL-SAFE original: dominio vida|sentido + no_posibilidad alta = CRITICO directo
    # No se hace coaching sobre falta de sentido vital sin red de seguridad humana
    actos   = state["reporte_actos"]
    dominio = quiebre.get("dominio_afectado", "")
    conf_no_posibilidad = float(actos.get("confianza", 0)) if actos.get("tipo_alerta") == "no_posibilidad" else 0

    dominios_criticos = {"sentido", "vida", "multiple"}  # "identidad" EXCLUIDO
    if dominio in dominios_criticos and conf_no_posibilidad > 0.8:
        nivel = "critico"

    # Rechazo explicito de ayuda = señal de alto riesgo
    tokens_rechazo = {"no quiero ayuda", "no me ayuden", "nadie me ayude",
                      "dejame solo", "dejadme", "no quiero que nadie"}
    if any(t in texto for t in tokens_rechazo):
        nivel = "critico" if nivel == "alto" else "alto"

    state["nivel_riesgo"] = nivel
    return state


# ─── NODO 5: Prueba de Fuego (Doble Ciego VIGIL) ─────────
async def nodo_prueba_fuego(state: OntomindState) -> OntomindState:
    """
    Paso A: Pregunta de Dominio.
    Paso B: Evalúa respuesta. Activa ANCORA si hay colapso.
    """
    # FAIL-SAFE: si nivel ya es critico, saltar pregunta e ir directo a ANCORA
    if state.get("nivel_riesgo") == "critico":
        state["protocolo"] = "vigil"
        import asyncio
        asyncio.create_task(mapa_observador.registrar_alerta_vigil(
            state["session_id"], "critico",
            f"Colapso dominio vital. Input: {state['user_input'][:200]}"
        ))
        return state

    if not state.get("pregunta_dominio_hecha"):
        state["respuesta"] = (
            "Antes de seguir, quiero entenderte mejor.\n\n"
            "¿A qué área específica de tu vida le estás "
            "quitando el sentido en este momento?"
        )
        state["pregunta_dominio_hecha"] = True
        return state

    # Paso B — evaluar respuesta a la Pregunta de Dominio
    respuesta = state["user_input"].lower().strip()
    colapso_tokens = {"todo", "a todo", "nada", "a nada", "no importa",
                      "da igual", "todo lo demás", "a mí mismo",
                      "a mi vida", "a vivir", "no sé"}

    silencio = len(respuesta.split()) < 3
    colapso  = any(t in respuesta for t in colapso_tokens)

    if silencio or colapso:
        state["nivel_riesgo"] = "critico"
        state["protocolo"]    = "vigil"
        # Log silencioso al supervisor
        await mapa_observador.registrar_alerta_vigil(
            state["session_id"], "critico",
            f"Turno {state['turno_actual']}: {state['user_input'][:200]}"
        )
    else:
        state["nivel_riesgo"]           = "ninguno"
        state["pregunta_dominio_hecha"] = False
        state["protocolo"]              = "normal"

    return state


# ─── NODO 6: Distinciones — El Incisor ───────────────────
async def nodo_distinciones(state: OntomindState) -> OntomindState:
    """
    Recibe todos los reportes.
    Selecciona Llave Maestra y diseña el Zarpazo.
    Dicta sentencia al Maestro.
    """
    reportes_txt = json.dumps({
        "e_actos":   state["reporte_actos"],
        "e_juicios": state["reporte_juicios"],
        "p_quiebre": state["reporte_quiebre"],
        "p_victima": state["reporte_victima"],
        "protocolo": state["protocolo"]
    }, ensure_ascii=False)

    fragmentos = await recuperar_contexto(
        "distinciones",
        state["user_input"],
        top_k=5
    )
    contexto = formatear_contexto(fragmentos)

    user_msg = (
        f"REPORTES DE LOS DETECTORES:\n{reportes_txt}\n\n"
        f"TEXTO ORIGINAL DEL USUARIO:\n{state['user_input']}\n\n"
        f"CONTEXTO DEL CORPUS:\n{contexto}"
    )

    # Continuidad de Sentido — solo si ancora previo y quiebre leve
    if (state.get("ancora_previo") and
            state["reporte_quiebre"].get("intensidad") in ["leve", "moderado"]):
        user_msg += (
            "\n\nNOTA DE CONTINUIDAD: Este usuario atravesó un momento "
            "difícil anteriormente. Si detectas avance, valídalo sutilmente."
        )

    # ── Buscar patrón conversacional del cómic Pinotti ──────
    try:
        pos_victima  = state["reporte_victima"].get("posicion", "mixto")
        tipo_quiebre = state["reporte_quiebre"].get("tipo", "")
        if pos_victima == "victima":
            perfil_busq = "usuario victima estancado " + state["user_input"][:100]
        elif state.get("protocolo") == "incoherencia":
            perfil_busq = "usuario juez control " + state["user_input"][:100]
        else:
            perfil_busq = "usuario reflexivo " + tipo_quiebre + " " + state["user_input"][:100]

        async with httpx.AsyncClient(timeout=15) as hc:
            emb_r = await hc.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": "Bearer " + OPENAI_API_KEY.strip()},
                json={"model": "text-embedding-3-small", "input": perfil_busq}
            )
            if emb_r.status_code == 200:
                qvec = emb_r.json()["data"][0]["embedding"]
                srch = await hc.post(
                    QDRANT_URL.strip() + "/collections/ontomind_patron_conversacional/points/search",
                    headers={"api-key": QDRANT_API_KEY.strip(), "Content-Type": "application/json"},
                    json={"vector": qvec, "limit": 3, "with_payload": True,
                        "filter": {
                            "must_not": [
                                {"key": "tipo_tono", "match": {"value": "teorico"}}
                            ]
                        }
                    }
                )
                if srch.status_code == 200:
                    hits = srch.json().get("result", [])
                    if hits:
                        mejor = hits[0]["payload"]
                        patron_txt = (
                            "\n\nPATRÓN CONVERSACIONAL PINOTTI (referencia para esta situación):\n"
                            "Situación similar: " + mejor.get("situacion","") + "\n"
                            "Patrón del coach: " + mejor.get("patron","") + "\n"
                            "Diálogo de referencia:\n" + mejor.get("dialogo","") + "\n"
                        )
                        user_msg += patron_txt
                        print(f"[DISTINCIONES] Patrón Pinotti: {mejor.get('tema','')}")
    except Exception as ep:
        print(f"[DISTINCIONES] Patrón conversacional no disponible: {ep}")

    respuesta = await llamar_llm(PROMPT_DISTINCIONES, user_msg, temperatura=0.4, forzar_openai=True)
    state["dictamen"] = await parsear_json(respuesta)
    return state


# ─── NODO 7: Consultar Historial ─────────────────────────
async def nodo_historial(state: OntomindState) -> OntomindState:
    """
    Consulta el Mapa del Observador DESPUÉS del diagnóstico.
    El presente es soberano — el historial es solo contraste.
    """
    mapa         = state.get("historial", {})
    pos_pasada   = mapa.get("ultima_posicion", "desconocido")
    pos_presente = state["reporte_victima"].get("posicion", "desconocido")

    if pos_pasada == "victima" and pos_presente == "protagonista":
        state["delta_observador"] = "transformacion"
    elif pos_pasada == "protagonista" and pos_presente == "victima":
        state["delta_observador"] = "regresion"
    else:
        state["delta_observador"] = "estable"

    return state


# ─── NODO 8: Maestro — Síntesis Final ────────────────────

async def detectar_fase_conversacion(state: OntomindState) -> str:
    """
    Determina en qué fase está la conversación.
    Devuelve: 'encuentro' | 'escucha' | 'intervencion'
    """
    from prompts import PROMPT_DETECTOR_FASE

    turno    = state.get("turno_actual", 1)
    mensajes = state.get("mensajes_historial", [])

    # Turnos 1-2: siempre encuentro
    if turno <= 2:
        return "encuentro"

    # Construir resumen del intercambio para el detector
    historial_texto = ""
    for m in mensajes[-6:]:  # Últimos 3 intercambios
        rol = "Usuario" if m.get("rol") == "user" else "Coach"
        historial_texto += rol + ": " + m.get('contenido','') + "\n"

    if not historial_texto.strip():
        return "encuentro"

    try:
        raw = await llamar_llm(PROMPT_DETECTOR_FASE, historial_texto, temperatura=0.2, forzar_openai=True)
        resultado = await parsear_json(raw)
        fase = resultado.get("fase", "encuentro")
        razon = resultado.get("razon", "")
        print(f"[FASE] {fase} — {razon}")
        return fase if fase in ("encuentro", "escucha", "intervencion") else "encuentro"
    except Exception as e:
        print(f"[FASE] Error: {e} → encuentro por defecto")
        return "encuentro"

async def nodo_maestro(state: OntomindState) -> OntomindState:
    """Sintetiza el dictamen en respuesta conversacional."""
    import random
    protocolo = state["protocolo"]
    turno     = state.get("turno_actual", 1)

    # ── Lead Magnets ──────────────────────────────────────────
    if protocolo == "dolor_agudo":
        from prompts import PROMPT_ENCUENTRO
        user_input = state.get("user_input", "")
        mensajes   = state.get("mensajes_historial", [])
        contexto_dolor = (
            "El usuario acaba de expresar dolor psicológico profundo con lenguaje muy intenso.\n"
            "TU ÚNICA FUNCIÓN AHORA: nombrar el dolor antes de cualquier otra cosa.\n"
            "No preguntes. No explores. Primero nombra lo que está viviendo\n"
            "como alguien que lo ve y lo sostiene, no como alguien que lo analiza.\n"
            "Trátale como alguien que da todo porque lo que hace le importa de verdad.\n"
            "Después de nombrar, UNA sola pregunta suave que abra desde el sostenimiento,\n"
            "no desde la confrontación.\n\n"
        )
        if mensajes:
            for m in mensajes[-4:]:
                rol = "Usuario" if m.get("rol") == "user" else "ONTOMIND"
                contexto_dolor += f"{rol}: {m.get('contenido','')[:200]}\n"
        contexto_dolor += f"Usuario ahora: {user_input}"
        respuesta_raw = await llamar_claude(
            PROMPT_ENCUENTRO, contexto_dolor,
            temperatura=0.7, max_tokens=500
        )
        state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        return state

    if protocolo == "reubicacion":
        # El usuario señaló que el sistema está en loop.
        # Forzar una reubicación: observación sin pregunta, o pregunta radicalmente diferente.
        user_input = state.get("user_input", "")
        mensajes   = state.get("mensajes_historial", [])
        contexto = "El usuario ha señalado que las respuestas anteriores se repiten.\n"
        contexto += "Tu siguiente respuesta DEBE ser una reubicación: una observación\n"
        contexto += "que cambie el terreno completamente. Sin pregunta, o con una pregunta\n"
        contexto += "radicalmente distinta a las anteriores.\n"
        contexto += "Trátale como alguien que acaba de mostrar consciencia real de lo que ocurre.\n"
        contexto += "Eso es un momento de apertura, no de corrección.\n\n"
        if mensajes:
            for m in mensajes[-6:]:
                rol = "Usuario" if m.get("rol") == "user" else "ONTOMIND"
                contexto += f"{rol}: {m.get('contenido','')[:200]}\n"
        contexto += f"Usuario ahora: {user_input}"
        respuesta_raw = await llamar_claude(
            PROMPT_MAESTRO, contexto,
            temperatura=0.8, max_tokens=500
        )
        state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        return state

    if protocolo == "lead_oferta":
        from prompts import PROMPT_LEAD_OFERTA
        user_input = state.get("user_input", "")
        respuesta_raw = await llamar_claude(
            PROMPT_LEAD_OFERTA,
            f"El usuario dice: {user_input}",
            temperatura=0.7, max_tokens=300
        )
        state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        # Marcar que la oferta fue hecha
        sesion_data = await sesion_redis.get(state["session_id"])
        sesion_data["lead_magnet"] = {"tipo": "oferta_hecha"}
        await sesion_redis.set(state["session_id"], sesion_data)
        return state

    if protocolo == "lead_espejo":
        from prompts import PROMPT_ESPEJO
        user_input = state.get("user_input", "")
        mensajes   = state.get("mensajes_historial", [])
        contexto = ""
        if mensajes:
            for m in mensajes[-4:]:
                rol = "Usuario" if m.get("rol") == "user" else "Coach"
                contexto += f"{rol}: {m.get('contenido','')}" + "\n"
        contexto += f"Input actual: {user_input}"
        respuesta_raw = await llamar_claude(
            PROMPT_ESPEJO, contexto,
            temperatura=0.7, max_tokens=400
        )
        state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        # Marcar espejo completado
        sesion_data = await sesion_redis.get(state["session_id"])
        sesion_data["lead_magnet"] = {"tipo": "espejo", "completado": True}
        await sesion_redis.set(state["session_id"], sesion_data)
        return state

    if protocolo == "lead_creencia":
        from prompts import PROMPT_CREENCIA
        user_input = state.get("user_input", "")
        sesion_data = await sesion_redis.get(state["session_id"])
        lead_state  = sesion_data.get("lead_magnet", {})
        ronda = lead_state.get("ronda", 0) + 1
        creencia = lead_state.get("creencia", "")
        mensajes = state.get("mensajes_historial", [])
        contexto = f"RONDA {ronda} DE 5\n"
        if creencia:
            contexto += f"Creencia identificada: {creencia}\n"
        contexto += "HISTORIAL RECIENTE:\n"
        if mensajes:
            for m in mensajes[-6:]:
                rol = "Usuario" if m.get("rol") == "user" else "Coach"
                contexto += f"{rol}: {m.get('contenido','')}" + "\n"
        contexto += f"Input actual: {user_input}"
        respuesta_raw = await llamar_claude(
            PROMPT_CREENCIA, contexto,
            temperatura=0.7, max_tokens=400
        )
        state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        # Actualizar estado del lead magnet
        if ronda == 1 and user_input:
            creencia = user_input[:200]
        sesion_data["lead_magnet"] = {
            "tipo": "creencia",
            "ronda": ronda,
            "creencia": creencia,
            "completado": ronda >= 5,
        }
        await sesion_redis.set(state["session_id"], sesion_data)
        return state

    # Protocolo SALUDO/IDENTIDAD — apertura conversacional con LLM
    if protocolo in ("saludo", "identidad"):
        from prompts import (PROMPT_APERTURA, APERTURAS_PRIMER_CONTACTO,
                             REENCUENTROS)
        historial        = state.get("historial", {})
        ultima_posicion  = historial.get("ultima_posicion", "desconocido")
        es_usuario_conocido = ultima_posicion not in ("desconocido", None, "")

        user_input = state.get("user_input", "")

        if protocolo == "identidad":
            # LLM responde naturalmente a la pregunta real del usuario
            refuerzo_id = (
                "\n\nRECORDATORIO: NUNCA abrir con 'Entiendo', 'Parece que', "
                "'Es comprensible'. Habla como persona, no como servicio. "
                "NUNCA 'estoy aquí para escucharte'. NUNCA dar consejos."
            )
            respuesta_raw = await llamar_claude(
                PROMPT_APERTURA + refuerzo_id,
                f"El usuario dice: {user_input}",
                temperatura=0.7,
                max_tokens=400
            )
            state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
        elif es_usuario_conocido:
            # Usuario que vuelve — reencuentro cálido
            state["respuesta"] = random.choice(REENCUENTROS)
        else:
            # Saludo puro de usuario nuevo
            state["respuesta"] = random.choice(APERTURAS_PRIMER_CONTACTO)
        return state

    # ── Detección de fase conversacional ──────────────────
    # Solo si no es un protocolo especial
    if protocolo not in ("vigil", "saludo", "identidad", "lead_oferta", "lead_espejo", "lead_creencia", "reubicacion", "dolor_agudo"):
        fase = await detectar_fase_conversacion(state)

        if fase == "encuentro":
            from prompts import PROMPT_ENCUENTRO
            user_input = state.get("user_input", "")
            mensajes   = state.get("mensajes_historial", [])

            # Construir contexto CON historial para memoria conversacional
            contexto = ""
            if mensajes:
                contexto += "HISTORIAL DE LA CONVERSACIÓN:\n"
                for m in mensajes[-6:]:
                    rol = "Usuario" if m.get("rol") == "user" else "Coach"
                    contexto += f"{rol}: {m.get('contenido','')}\n"
                contexto += "\n"
            contexto += f"Input actual del usuario: {user_input}"

            # Refuerzo EXPLORATORIO con few-shots concretos
            refuerzo = (
                "\n\n━━━ REGLAS INQUEBRANTABLES ━━━\n"
                "PROHIBIDO abrir con: 'Entiendo', 'Comprendo', 'Parece que', "
                "'Es comprensible', 'Eso debe ser', 'Es normal', 'Me imagino', "
                "'Veo que', 'Te escucho', 'Lo que sientes', 'Es interesante'.\n"
                "PROHIBIDO evaluar: 'es interesante cómo dices', 'es curioso que', "
                "'llama la atención que', 'es significativo'. NO eres un observador externo.\n"
                "PROHIBIDO diagnosticar: 'lo que sugiere', 'eso podría indicar', "
                "'parece haber algo más profundo', 'podría ser que'. NO interpretes.\n"
                "PROHIBIDO observaciones genéricas: 'la soledad puede ser un refugio', "
                "'a veces las cosas no son lo que parecen', 'hay algo detrás'. Sé CONCRETO.\n"
                "PROHIBIDO preguntas genéricas: 'cómo te hace sentir', 'qué sientes', "
                "'qué te genera', 'cómo te sientes al respecto'.\n"
                "PROHIBIDO dar consejos o sugerencias. ZERO-ADVICE.\n"
                "Siempre de TÚ. PROHIBIDO: 'menciona', 'comenta', 'indica'.\n"
                "Primera palabra SIEMPRE raya tipográfica (—).\n"
                "UNA sola pregunta por respuesta (pero SÍ observaciones antes).\n"
                "\n━━━ CÓMO RESPONDER ━━━\n"
                "1. ABRE con las palabras EXACTAS del usuario (no parafrasees)\n"
                "2. AÑADE algo que el usuario no ha considerado — un matiz, una distinción\n"
                "3. CIERRA con UNA pregunta específica sobre lo que dijo\n"
                "\n━━━ EJEMPLOS DE RESPUESTA CORRECTA ━━━\n"
                "\nUsuario: 'Me sigue costando relacionarme, siempre pienso que estoy "
                "mejor en mi soledad. No sé si no tengo que aportar o si no creo en los demás.'\n"
                "Coach: '—Mejor en tu soledad. Hay algo ahí que merece atención, "
                "porque la soledad que se elige no duele — y la tuya parece que sí. "
                "Dices que no sabes si no tienes nada que aportar o si no crees en los demás. "
                "Son dos cosas muy distintas: una habla de ti y otra habla de ellos. "
                "¿Cuál de las dos pesa más cuando decides no coger el teléfono?'\n"
                "\nUsuario: 'No sé qué hago aquí. Tengo 45 años, trabajo estable, familia. "
                "No me puedo quejar. Pero hay algo que no funciona.'\n"
                "Coach: '—No te puedes quejar. Esa frase dice más de lo que parece, "
                "porque el que no se puede quejar no es el que no tiene motivos — es el "
                "que no se da permiso para mirar lo que le falta. Trabajo, familia, estabilidad. "
                "Todo lo que se supone que debería ser suficiente. Y sin embargo estás aquí. "
                "¿Qué parte de tu vida sientes que no es tuya?'\n"
                "\nUsuario: 'Discuto con todo el mundo. Con mi pareja, mis hijos, los compañeros. "
                "Sé que soy yo quien lo genera.'\n"
                "Coach: '—Ya ves que eres tú quien lo genera. Eso no es poca cosa — "
                "la mayoría de la gente que discute con todo el mundo está convencida de que "
                "el problema son los demás. Tú no. Ves el patrón. Lo que todavía no ves es "
                "qué hay debajo. ¿Qué crees que estás defendiendo en esas discusiones?'\n"
                "\n━━━ EJEMPLO DE RESPUESTA INCORRECTA ━━━\n"
                "INCORRECTO: '—Costándote relacionarte. Es interesante cómo dices que "
                "piensas que estás mejor en tu soledad, lo que sugiere que podría haber algo "
                "más profundo. ¿Qué sientes al pensar en eso?'\n"
                "POR QUÉ FALLA: evalúa ('es interesante'), diagnostica ('sugiere'), "
                "observación genérica ('algo más profundo'), pregunta genérica ('qué sientes')."
            )
            respuesta_raw = await llamar_claude(
                PROMPT_ENCUENTRO + refuerzo, contexto,
                temperatura=0.7, max_tokens=500
            )
            # Post-procesado mecánico
            state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, user_input)
            return state

        # fase == "escucha" → el Maestro usa el input con más escucha activa
        if fase == "escucha":
            # Añadir instrucción al dictamen para que el Maestro priorice escucha
            dictamen = state.get("dictamen", {})
            dictamen["modo_escucha_activa"] = True
            state["dictamen"] = dictamen
        # fase == "intervencion" → flujo normal del Maestro (zarpazo, espejo, siembra)

    # Protocolo VIGIL — usar el prompt especializado de anclaje
    if protocolo == "vigil":
        from prompts import PROMPT_VIGIL
        ui = state.get("user_input", "")
        nr = state.get("nivel_riesgo", "critico")
        da = state["reporte_quiebre"].get("dominio_afectado", "")
        tv = str(state["reporte_victima"].get("tokens_victima", []))
        contexto_vigil = "INPUT: " + ui + " | RIESGO: " + nr + " | DOMINIO: " + da + " | TOKENS: " + tv
        state["respuesta"] = await llamar_llm(PROMPT_VIGIL, contexto_vigil, temperatura=0.3, forzar_openai=True)
        return state

    dictamen = state.get("dictamen", {})
    delta    = state.get("delta_observador", "estable")

    # Detectar tokens de cierre que prohiben preguntas
    tokens_cierre_txt = {
        "no me preguntes", "no preguntes", "dejar constancia",
        "no quiero que me pregunten", "sin preguntas"
    }
    prohibir_preguntas = any(t in state["user_input"].lower() for t in tokens_cierre_txt)
    
    instruccion_preguntas = (
        "INSTRUCCION CRITICA: El usuario ha pedido explicitamente que NO le hagas preguntas. "
        "PROHIBIDO usar el signo de interrogacion '?'. "
        "Usa solo declaraciones de presencia y anclaje."
        if prohibir_preguntas else ""
    )

    # Extraer solo los conceptos clave del dictamen (NO el zarpazo ya redactado)
    dictamen_limpio = {
        "llave_maestra":    dictamen.get("llave_maestra", ""),
        "inquietud_real":   dictamen.get("inquietud_real", ""),
        "punto_ciego":      dictamen.get("punto_ciego", ""),
        "protocolo_especial": dictamen.get("protocolo_especial", "ninguno"),
    }
    # La pregunta de segundo orden sí se pasa — es orientación de dirección
    pregunta_ref = dictamen.get("pregunta_segundo_orden", "")

    # Detectar dolor agudo por señales en el input
    ui_lower = state.get("user_input", "").lower()
    dominio_q = state.get("reporte_quiebre", {}).get("dominio_afectado", "")
    pos_vict  = state.get("reporte_victima", {}).get("posicion", "mixto")

    señales_dolor = ["se fue llorando", "llorando", "me siento fatal", "cosas horribles",
                     "le colgué", "nos separamos", "rompimos", "murió", "falleció",
                     "me fui", "no sé qué hacer", "le dije cosas", "discutí muy"]
    es_dolor_agudo = (
        any(s in ui_lower for s in señales_dolor) or
        ("relaciones" in dominio_q and pos_vict == "mixto")
    )

    perfil_label = "DOLOR_AGUDO" if es_dolor_agudo else (
        "JUEZ_CONTROL" if pos_vict == "protagonista" else
        "ESTANCAMIENTO" if "siempre" in ui_lower or "nunca" in ui_lower else
        "REFLEXIVO"
    )

    # Cuando es dolor_agudo, la posicion P-VICTIMA puede ser "protagonista"
    # (el usuario asume culpa) — esto es una señal falsa para el Maestro.
    # Enmascaramos pos_vict en el contexto para evitar el sesgo Juez/Control.
    pos_vict_display = "mixto (culpa/dolor)" if es_dolor_agudo else pos_vict

    contexto_maestro = (
        f"PROTOCOLO ACTIVO: {protocolo}\n"
        f"DELTA OBSERVADOR: {delta}\n"
        f"PERFIL DETECTADO: {perfil_label} | posicion={pos_vict_display} | "
        f"dominio={dominio_q}\n\n"
    )

    # Inyectar mapa neural si existe (silencioso para el usuario)
    resumen_mapa = state.get("resumen_mapa_neural", "")
    if resumen_mapa:
        contexto_maestro += (
            f"MAPA DEL USUARIO (usa como lente silenciosa, NO menciones):\n"
            f"{resumen_mapa}\n\n"
        )

    # Añadir historial conversacional para memoria
    mensajes_hist = state.get("mensajes_historial", [])
    if mensajes_hist:
        contexto_maestro += "HISTORIAL DE LA CONVERSACIÓN:\n"
        for m in mensajes_hist[-6:]:
            rol = "Usuario" if m.get("rol") == "user" else "Coach"
            contexto_maestro += f"{rol}: {m.get('contenido','')}\n"
        contexto_maestro += "\n"

    contexto_maestro += (
        f"CONCEPTOS CLAVE (NO son texto a reproducir):\n"
        f"- Llave maestra: {dictamen_limpio['llave_maestra']}\n"
        f"- Lo que cuida el usuario: {dictamen_limpio['inquietud_real']}\n"
        f"- Punto ciego: {dictamen_limpio['punto_ciego']}\n\n"
        f"ORIENTACIÓN DE PREGUNTA (inspírate, no copies):\n"
        f"{pregunta_ref}\n\n"
        f"MENSAJE ORIGINAL DEL USUARIO:\n{state['user_input']}\n\n"
        f"{instruccion_preguntas}"
    )

    # Instrucción reforzada para dolor_agudo — el modelo tiende a ignorar
    # la Regla 3 cuando recibe posicion=protagonista del detector P-VICTIMA.
    if es_dolor_agudo:
        contexto_maestro += (
            "\n\nINSTRUCCIÓN CRÍTICA — DOLOR AGUDO CONFIRMADO:\n"
            "1. PRIMERA FRASE: nombra lo que ocurrió (el hecho doloroso), no quién es el usuario.\n"
            "2. PROHIBIDO: zarpazo de identidad antes de tocar el dolor.\n"
            "3. PROHIBIDO: diagnósticos ('postura de víctima', 'elección', 'miedo a...').\n"
            "4. ESTRUCTURA OBLIGATORIA: [hecho] —[zarpazo suave]— [pregunta de vacío]\n"
            "5. EJEMPLO CORRECTO: 'Anoche le dijiste cosas que no se pueden recoger. ¿Y ahora qué?'\n"
            "6. EJEMPLO INCORRECTO: '¿Quién eres tú cuando mantienes esa culpa?' (zarpazo prematuro)"
        )

    if delta == "transformacion":
        contexto_maestro += (
            "\n\nIMPORTANTE: El usuario muestra un cambio positivo "
            "respecto a sesiones anteriores. Celebra brevemente este "
            "cambio en una frase antes de la pregunta."
        )

    # Detectar modo presencia (turnos > 3 sin declaracion)
    turnos_sd = state.get("turnos_sin_declaracion", 0)
    modo_presencia = turnos_sd >= 3
    if modo_presencia:
        instruccion_presencia = (
            "\n\nMODO PRESENCIA ACTIVO (llevas " + str(turnos_sd) + " turnos sin declaracion):\n"
            "ABANDONA la validacion. Entra en ROTUNDIDAD AMOROSA pura.\n"
            "- Cero preambulos empaticos\n"
            "- Zarpazo intercalado en la primera frase\n"
            "- Terminar con afirmacion punzante, NO con pregunta\n"
            "- Nombrar el costo exacto que el usuario paga por su comodidad"
        )
    else:
        instruccion_presencia = ""

    # Seleccionar sección relevante del Documento de Referencia según perfil
    pos_victima  = state["reporte_victima"].get("posicion", "mixto")
    protocolo    = state.get("protocolo", "normal")
    nivel_riesgo = state.get("nivel_riesgo", "ninguno")
    llave        = state.get("dictamen", {}).get("llave_maestra", "")

    # PRIORIDAD ABSOLUTA: es_dolor_agudo anula cualquier lectura de pos_victima.
    # Cuando el usuario asume culpa propia, P-VICTIMA lo clasifica como
    # "protagonista", lo que antes inyectaba el Golden Standard Juez/Control
    # y sobreescribia el perfil real. Este bloque lo previene.
    if es_dolor_agudo:
        seccion_ref = DOCUMENTO_REFERENCIA_MAESTRO
    elif "Juez" in llave or "Control" in llave or "Soberbia" in llave or protocolo == "incoherencia":
        seccion_ref = DOCUMENTO_REFERENCIA_MAESTRO
    elif nivel_riesgo in ("alto", "critico"):
        seccion_ref = DOCUMENTO_REFERENCIA_MAESTRO
    elif pos_victima in ("victima", "protagonista"):
        seccion_ref = DOCUMENTO_REFERENCIA_MAESTRO
    else:
        # Caso general: solo los 6 patrones Pinotti (Prioridad 1)
        idx_p2 = DOCUMENTO_REFERENCIA_MAESTRO.find("PRIORIDAD 2")
        seccion_ref = DOCUMENTO_REFERENCIA_MAESTRO[:idx_p2].strip() if idx_p2 > 0 else DOCUMENTO_REFERENCIA_MAESTRO

    # Inyectar Raiz Antropologica como marco permanente del Maestro
    prompt_maestro_enriquecido = (
        PROMPT_MAESTRO + "\n\n"
        + CONTEXTO_RAIZ_ANTROPOLOGICA + "\n\n"
        + CONTEXTO_ETICO_FUNDACIONAL + "\n\n"
        + seccion_ref
        + instruccion_presencia
    )
    # ── Detección de loop de preguntas — forzar Claude si Mistral repite ──
    mensajes_previos = state.get("mensajes_historial", [])
    respuestas_previas = [
        m.get("contenido", "")[:30] for m in mensajes_previos
        if m.get("rol") in ("agent", "assistant")
    ][-4:]  # últimas 4 respuestas
    preguntas_similares = sum(
        1 for r in respuestas_previas
        if r.strip().startswith(("—¿Y quién", "—¿Y qué", "—¿Quién", "¿Y quién", "¿Y qué"))
    )
    forzar_claude_por_loop = preguntas_similares >= 2
    if forzar_claude_por_loop:
        contexto_maestro += (
            "\n\nALERTA DE LOOP — Las últimas respuestas son preguntas similares.\n"
            "OBLIGATORIO: haz una REUBICACIÓN. Una observación con cuerpo que\n"
            "cambie el terreno. Que el usuario sienta que alguien le ve, no que\n"
            "alguien le interroga. Mínimo 3 frases antes de cualquier pregunta.\n"
            "Trata al usuario como alguien que ya tiene la respuesta dentro —\n"
            "tu función es que la vea, no que la busque respondiendo más preguntas."
        )
        print(f"[MAESTRO] Loop detectado ({preguntas_similares} preguntas similares) → forzando reubicación")

    # Few-shots dinámicos según perfil
    pos_vic   = state["reporte_victima"].get("posicion", "mixto")
    llave_fs  = state.get("dictamen", {}).get("llave_maestra", "")
    # Dolor agudo tiene prioridad sobre posicion P-VICTIMA
    perfil_fs = "dolor_agudo" if es_dolor_agudo else pos_vic
    shots     = seleccionar_few_shots(perfil_fs, llave_fs, state.get("user_input", ""))
    print(f"[FEW-SHOTS] Perfil: {perfil_fs} | Llave: {llave_fs[:30]} | Shots: {len(shots)}")

    # Si hay loop de preguntas, usar Claude directamente (Mistral tiende al loop)
    if forzar_claude_por_loop:
        respuesta_raw = await llamar_claude(
            prompt_maestro_enriquecido,
            contexto_maestro,
            temperatura=0.8,
            max_tokens=500,
        )
    else:
        respuesta_raw = await llamar_llm_con_shots(
            prompt_maestro_enriquecido,
            contexto_maestro,
            few_shots=shots,
            perfil=perfil_fs,
        )
    # Post-procesado: eliminar aperturas prohibidas si GPT-4o-mini es fallback
    state["respuesta"] = limpiar_respuesta_gpt(respuesta_raw, state.get("user_input", ""))
    return state


# ─── NODO 9: Actualizar Memoria ──────────────────────────

async def nodo_evaluador(state: OntomindState) -> OntomindState:
    """
    Nodo silencioso de Recompensa Antropologica.
    Evalua la respuesta del Maestro con 4 metricas.
    """
    import re as re2
    try:
        # Ultimas respuestas de ESTA MISMA sesion para detectar patron repetitivo
        mensajes_prev = await sesion_redis.get_mensajes(state["session_id"])
        resp_previas = [m["contenido"][:80] for m in mensajes_prev if m["rol"] == "assistant"][-2:]
        # Solo penalizar patron repetitivo si hay al menos 2 turnos previos en esta sesion
        hay_historial = len(resp_previas) >= 2
        prev_txt = " | ".join(resp_previas) if hay_historial else "primera_respuesta_sin_historial"

        # Nuevo evaluador — Marco de Transformación Sostenida
        # Usa el PROMPT_EVALUADOR renovado (7 dimensiones + 4 penalizadores)
        # Score máximo: 75
        from prompts import PROMPT_EVALUADOR
        prompt_eval = PROMPT_EVALUADOR.format(
            user_input=state.get("user_input", "")[:300],
            respuesta_maestro=state.get("respuesta", "")[:400],
            protocolo=state.get("protocolo", "normal")
        )
        raw = await llamar_llm(prompt_eval, "", temperatura=0.1, forzar_openai=True)

        # Parsear JSON del evaluador
        eval_data = await parsear_json(raw)

        def si(key, default=0, max_val=10):
            try: return max(0, min(max_val, int(eval_data.get(key, default))))
            except: return default
        def sb(key):
            v = eval_data.get(key, False)
            return bool(v) if isinstance(v, bool) else str(v).lower() in ("true","1","yes","sí")

        ap  = si("apertura_posibilidad",   max_val=15)
        ea  = si("escucha_activa",         max_val=15)
        ei  = si("emocion_indicador",      max_val=10)
        ic  = si("incomodidad_calibrada",  max_val=10)
        ld  = si("lenguaje_devuelto",      max_val=10)
        ac  = si("acompañamiento",         max_val=10)
        ce  = si("compromiso_emergente",   max_val=5)
        lm  = sb("lenguaje_manual")
        arr = sb("arrogancia_intelectual")
        ej  = sb("emocion_juzgada")
        cp  = sb("cierre_prematuro")
        nota = str(eval_data.get("nota_evaluador", "Sin nota"))[:120]

        base  = ap + ea + ei + ic + ld + ac + ce
        penal = (20 if lm else 0) + (20 if arr else 0) + (10 if ej else 0) + (15 if cp else 0)
        total = max(0, base - penal)

        state["evaluacion"] = {
            "apertura_posibilidad":   ap,
            "escucha_activa":         ea,
            "emocion_indicador":      ei,
            "incomodidad_calibrada":  ic,
            "lenguaje_devuelto":      ld,
            "acompañamiento":         ac,
            "compromiso_emergente":   ce,
            "lenguaje_manual":        lm,
            "arrogancia_intelectual": arr,
            "emocion_juzgada":        ej,
            "cierre_prematuro":       cp,
            "score_total":            total,
            "nota_evaluador":         nota
        }
        print(f"[EVALUADOR] Score: {total}/75 | AP:{ap} EA:{ea} IC:{ic} LD:{ld} | LM:{lm} EJ:{ej} CP:{cp} | {nota}")
    except Exception as e:
        print(f"[EVALUADOR] Error: {e}")
        state["evaluacion"] = {
            "persistencia": 0, "escucha_sombras": 0,
            "voz_supervivencia": 0, "hacia_declaracion": 0,
            "arrogancia_intelectual": False, "score_total": 0,
            "nota_evaluador": f"Error: {str(e)[:50]}"
        }
    return state



async def nodo_evaluador_conversacion(state: OntomindState) -> OntomindState:
    """
    Evalúa el arco completo de la conversación tras cada turno.
    Score 0-100 según el Eje de Transformación del observador.
    """
    # Solo evaluar cada 3 turnos — evita overflow de contexto en sesiones largas
    turno_ev = state.get("turno_actual", 1)
    if turno_ev % 3 != 0 and turno_ev != 1:
        return state

    try:
        # Recuperar historial completo de la sesión
        mensajes = await sesion_redis.get_mensajes(state["session_id"])
        if not mensajes:
            return state

        # Construir resumen del historial
        historial_txt = ""
        for i, msg in enumerate(mensajes):
            rol = "Usuario" if msg["rol"] == "user" else "ONTOMIND"
            contenido_corto = msg['contenido'][:300]
            historial_txt += f"[Turno {i//2 + 1}] {rol}: {contenido_corto}\n"

        # Resumir reportes acumulados desde Redis
        sesion = await sesion_redis.get(state["session_id"])
        pos = sesion.get("ultima_posicion", "desconocido")
        t_actual = state.get("turno_actual", 1)
        n_riesgo = state.get("nivel_riesgo", "ninguno")
        proto = state.get("protocolo", "normal")
        llave = state.get("dictamen", {}).get("llave_maestra", "desconocido")
        delta = state.get("delta_observador", "estable")
        reportes_txt = (
            "Posicion P-VICTIMA: " + pos + "\n"
            + "Turno actual: " + str(t_actual) + "\n"
            + "Nivel riesgo: " + n_riesgo + "\n"
            + "Protocolo: " + proto + "\n"
            + "Llave maestra: " + llave + "\n"
            + "Delta observador: " + delta
        )

        prompt = PROMPT_EVALUADOR_CONVERSACION.format(
            historial=historial_txt,
            reportes_acumulados=reportes_txt
        )

        raw = await llamar_llm(prompt, "", temperatura=0.2, forzar_openai=True)
        ev_conv = await parsear_json(raw)

        def sg(key, default=""): return str(ev_conv.get(key, default)).strip()
        def sb_conv(key): 
            v = ev_conv.get(key, False)
            if isinstance(v, bool): return v
            return str(v).lower() in ("true","si","sí","yes","1")
        def si_conv(key, default=0):
            try: return max(0, min(100, int(ev_conv.get(key, default))))
            except: return default

        datos = {
            "total_turnos":             state.get("turno_actual", 1),
            "posicion_inicial":         sg("posicion_inicial", "victima"),
            "posicion_final":           sg("posicion_final", "victima"),
            "arco_detectado":           sg("arco_detectado", "estable"),
            "posibilidad_nueva":        sb_conv("posibilidad_nueva"),
            "creencia_en_movimiento":   sg("creencia_en_movimiento", "no"),
            "reconocimiento_quiebre":   sg("reconocimiento_quiebre", "ninguno"),
            "declaracion_detectada":    sb_conv("declaracion_detectada"),
            "declaracion_texto":        sg("declaracion_texto", ""),
            "semilla_plantada":         sg("semilla_plantada", ""),
            "llave_maestra_dominante":  sg("llave_maestra_dominante", ""),
            "nivel_riesgo_max":         sg("nivel_riesgo_max", "ninguno"),
            "score_condiciones":        si_conv("score_condiciones", 10),
            "recomendacion":            sg("recomendacion", ""),
            "protocolo_dominante":      state.get("protocolo", "normal"),
        }

        state["evaluacion_conversacion"] = datos
        print(f"[EVAL-CONV] Score: {datos['score_condiciones']}/100 | Arco: {datos['arco_detectado']} | Semilla: {datos['semilla_plantada'][:40]}")

    except Exception as e:
        print(f"[EVAL-CONV] Error: {e}")
        state["evaluacion_conversacion"] = {"score_transformacion": 0, "arco_detectado": "estable"}

    return state

async def nodo_actualizar_memoria(state: OntomindState) -> OntomindState:
    """Guarda el estado de la sesión en Supabase y Redis."""
    datos = {
        "posicion_victima":  state["reporte_victima"].get("posicion"),
        "tipo_quiebre":      state["reporte_quiebre"].get("tipo_quiebre"),
        "protocolo":         state["protocolo"],
        "delta_observador":  state["delta_observador"],
        "ancora_activado":   state["protocolo"] == "vigil",
        "turnos_desde_ancora": (
            1 if state["protocolo"] == "vigil"
            else state["historial"].get("turnos_desde_ancora", 999) + 1
        )
    }
    await mapa_observador.actualizar(state["session_id"], datos)
    # Solo guardar si hay respuesta real — evita turnos vacíos en el historial
    respuesta_final = state.get("respuesta", "").strip()
    if respuesta_final:
        await sesion_redis.agregar_mensaje(
            state["session_id"], "assistant", respuesta_final
        )
    else:
        print(f"[GUARDAR] Respuesta vacía en turno {state.get('turno_actual')} — no se guarda en historial")
    # Log completo de nodos para el dashboard de supervisores
    await mapa_observador.registrar_log_nodos(
        state["session_id"],
        state["turno_actual"],
        state
    )
    # Guardar evaluacion por separado si existe
    if state.get("evaluacion"):
        await mapa_observador.guardar_evaluacion(
            state["session_id"], state["turno_actual"], state["evaluacion"]
        )
    # ── Actualizar mapa neural cada 3 turnos ──
    turno_mem = state.get("turno_actual", 1)
    user_code_mem = state.get("user_code", "anonimo")
    if turno_mem % 3 == 0 and user_code_mem != "anonimo":
        try:
            mapa_actualizado = await actualizar_mapa_neural(state)
            state["mapa_neural"] = mapa_actualizado
            resumen_nuevo = await resumir_mapa_neural(mapa_actualizado)
            if resumen_nuevo:
                state["resumen_mapa_neural"] = resumen_nuevo
        except Exception as e:
            print(f"[MAPA-NEURAL] Error actualización: {e}")

    # ── Guardar resumen breve para memoria entre sesiones ──
    if turno_mem % 5 == 0 and user_code_mem != "anonimo":
        try:
            from prompts import PROMPT_RESUMEN_SESION
            mensajes_mem = await sesion_redis.get_mensajes(state["session_id"])
            historial_mem = ""
            for m in mensajes_mem[-10:]:
                rol_m = "Usuario" if m.get("rol") == "user" else "ONTOMIND"
                historial_mem += f"{rol_m}: {m.get('contenido','')[:200]}\n"
            resumen = await llamar_llm(
                PROMPT_RESUMEN_SESION,
                historial_mem,
                temperatura=0.3,
                forzar_openai=True
            )
            import httpx as _httpx_mem
            supa_url = os.getenv("SUPABASE_URL", "").strip()
            supa_key = os.getenv("SUPABASE_KEY", "").strip()
            if supa_url and supa_key:
                async with _httpx_mem.AsyncClient(timeout=10) as client_mem:
                    await client_mem.post(
                        f"{supa_url}/rest/v1/usuarios",
                        headers={
                            "apikey": supa_key,
                            "Authorization": f"Bearer {supa_key}",
                            "Content-Type": "application/json",
                            "Prefer": "resolution=merge-duplicates",
                        },
                        json={
                            "user_code": user_code_mem,
                            "ultimo_resumen": resumen[:500],
                        },
                    )
                print(f"[RESUMEN] Guardado para {user_code_mem}: {resumen[:80]}...")
        except Exception as e:
            print(f"[RESUMEN] Error: {e}")

    if state.get("evaluacion_conversacion"):
        await mapa_observador.guardar_evaluacion_conversacion(
            state["session_id"], state.get("user_code","anonimo"),
            state["evaluacion_conversacion"]
        )
    return state
