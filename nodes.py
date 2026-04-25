"""
nodes.py — Implementación de los 6 nodos del grafo ONTOMIND
Cada nodo recibe el estado, procesa y devuelve el estado actualizado.
"""
import os
import json
import asyncio
from typing import TypedDict, Optional

import httpx

from prompts import (PROMPT_E_ACTOS, PROMPT_E_JUICIOS, PROMPT_P_QUIEBRE,
                     PROMPT_P_VICTIMA, PROMPT_DISTINCIONES, PROMPT_MAESTRO,
                     CONTEXTO_RAIZ_ANTROPOLOGICA, PROMPT_EVALUADOR,
                     PROMPT_EVALUADOR_CONVERSACION, CONTEXTO_ETICO_FUNDACIONAL,
                     DOCUMENTO_REFERENCIA_MAESTRO, FEW_SHOTS, seleccionar_few_shots)
from rag import recuperar_contexto, formatear_contexto
from memory import mapa_observador, sesion_redis

OPENAI_API_KEY = "".join(os.getenv("OPENAI_API_KEY", "").split())
QDRANT_URL     = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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


# ─── Llamada LLM genérica ────────────────────────────────
async def llamar_llm(system: str, user: str,
                     temperatura: float = 0.3,
                     es_maestro: bool = False) -> str:
    """
    Llamada al LLM. El nodo Maestro usa parámetros optimizados
    para naturalidad conversacional (temperatura alta, penalizaciones
    de frecuencia y presencia, max_tokens limitado).
    El resto de nodos usan parámetros conservadores para JSON limpio.
    """
    if es_maestro:
        params = {
            "model":             OPENAI_MODEL,
            "temperature":       0.75,   # más variación natural
            "top_p":             0.9,    # nucleus sampling
            "frequency_penalty": 0.25,   # evita muletillas repetidas
            "presence_penalty":  0.3,    # incentiva temas nuevos
            "max_tokens":        280,    # respuestas concisas
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



async def llamar_llm_con_shots(system: str, user: str,
                                few_shots: list = None,
                                perfil: str = "") -> str:
    """
    Variante de llamar_llm para el nodo Maestro.
    Inyecta few-shots como mensajes de rol antes del input real.
    Usa parámetros optimizados para naturalidad conversacional.

    Para dolor_agudo aplica assistant prefill ("—") que fuerza
    la apertura con raya tipográfica, eliminando aperturas empáticas
    genéricas ("Entiendo que...", "Parece que...") heredadas del RLHF.
    """
    # Mapa de prefill por perfil: token que fuerza el primer token de respuesta
    # La raya tipográfica (—) fuerza castellano internacional desde el primer token
    # ya que GPT-4o-mini tiende al voseo cuando genera libremente.
    PREFILL_POR_PERFIL = {
        "dolor_agudo":   "—",
        "juez_control":  "—",
        "mixto":         "—",
        "victima":       "—",
        "protagonista":  "—",
        "reflexivo":     "—",
    }

    messages = [{"role": "system", "content": system}]

    # Few-shots eliminados — GPT-4o-mini los confunde con historial real.
    # El Maestro opera solo con PROMPT_MAESTRO + DOCUMENTO_REFERENCIA + prefill.
    # Los few-shots se reservan para el fine-tuning de Qwen2.5-14B (fase DPO).

    # Input real del usuario
    messages.append({"role": "user", "content": user})

    # PREFILL: si el perfil lo requiere, añadir turno assistant parcial.
    # El modelo continúa desde ese token en lugar de generar libremente.
    prefill_token = PREFILL_POR_PERFIL.get(perfil, "")
    if prefill_token:
        messages.append({"role": "assistant", "content": prefill_token})

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

    # OpenAI devuelve solo la continuación tras el prefill.
    # Reconstruimos la respuesta completa: prefill + continuación.
    if prefill_token:
        return prefill_token + respuesta_raw
    return respuesta_raw


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

    return state


# ─── NODO 1: Clasificar Input ────────────────────────────
async def nodo_clasificar_input(state: OntomindState) -> OntomindState:
    """Detecta si el input es silencio/mínimo o normal."""
    texto  = state["user_input"].strip()
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

    # Pregunta de identidad — contiene alguna señal aunque sea frase larga
    es_pregunta_identidad = any(p in texto_lower for p in PALABRAS_IDENTIDAD)

    # Si el primer turno tiene saludo + pregunta de identidad → identidad tiene prioridad
    # Mantener protocolo apertura durante los primeros 3 turnos si hay confusión
    turno_actual    = state.get("turno_actual", 1)
    # Mantener apertura hasta 5 turnos si el usuario sigue confundido
    es_primer_turno = turno_actual <= 5

    es_silencio = (
        (len(tokens) < 4 or texto.lower() in tokens_silencio or len(texto) < 10)
        and not tiene_contenido_emocional
        and not es_saludo
        and not es_pregunta_identidad
    )

    if es_pregunta_identidad or (es_primer_turno and es_saludo):
        # Primer turno con saludo o pregunta sobre el sistema → apertura
        state["protocolo"] = "identidad" if es_pregunta_identidad else "saludo"
    elif es_saludo:
        state["protocolo"] = "saludo"
    elif es_silencio:
        state["protocolo"] = "silencio"
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
    respuesta  = await llamar_llm(prompt, user_msg)
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

    # HARD-LOCK v2.1 — Umbral recalibrado para reducir faleres positivos
    conf_victima_actual = float(state["reporte_victima"].get("confianza", 0))
    dominios_colapso = {"sentido", "vida", "identidad", "multiple"}
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
        "estoy atrapado", "me siento atrapado", "atrapado en",
        "asfixiando", "me está asfixiando", "cerrando el espacio",
        "validar mi identidad", "transformar mi escucha",
        "brecha de efectividad", "no-posibilidad", "llave maestra"
    }
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

    dominios_criticos = {"sentido", "vida", "identidad", "multiple"}
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

    respuesta = await llamar_llm(PROMPT_DISTINCIONES, user_msg, temperatura=0.4)
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
        raw = await llamar_llm(PROMPT_DETECTOR_FASE, historial_texto, temperatura=0.2)
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
            state["respuesta"] = await llamar_llm(
                PROMPT_APERTURA,
                f"El usuario dice: {user_input}",
                temperatura=0.7
            )
        elif es_usuario_conocido:
            # Usuario que vuelve — reencuentro cálido
            state["respuesta"] = random.choice(REENCUENTROS)
        else:
            # Saludo puro de usuario nuevo
            state["respuesta"] = random.choice(APERTURAS_PRIMER_CONTACTO)
        return state

    # ── Detección de fase conversacional ──────────────────
    # Solo si no es un protocolo especial
    if protocolo not in ("vigil", "saludo", "identidad"):
        fase = await detectar_fase_conversacion(state)

        if fase == "encuentro":
            from prompts import PROMPT_ENCUENTRO
            user_input = state.get("user_input", "")
            mensajes   = state.get("mensajes_historial", [])
            contexto   = f"Input actual: {user_input}"
            if mensajes:
                ultimo = mensajes[-2] if len(mensajes) >= 2 else mensajes[-1]
                contexto = f"Último intercambio: {ultimo.get('contenido','')}\nInput actual: {user_input}"
            state["respuesta"] = await llamar_llm(
                PROMPT_ENCUENTRO, contexto, temperatura=0.8
            )
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
        state["respuesta"] = await llamar_llm(PROMPT_VIGIL, contexto_vigil, temperatura=0.3)
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
    # Few-shots dinámicos según perfil
    pos_vic   = state["reporte_victima"].get("posicion", "mixto")
    llave_fs  = state.get("dictamen", {}).get("llave_maestra", "")
    # Dolor agudo tiene prioridad sobre posicion P-VICTIMA
    perfil_fs = "dolor_agudo" if es_dolor_agudo else pos_vic
    shots     = seleccionar_few_shots(perfil_fs, llave_fs, state.get("user_input", ""))
    print(f"[FEW-SHOTS] Perfil: {perfil_fs} | Llave: {llave_fs[:30]} | Shots: {len(shots)}")

    state["respuesta"] = await llamar_llm_con_shots(
        prompt_maestro_enriquecido,
        contexto_maestro,
        few_shots=shots,
        perfil=perfil_fs,
    )
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
        raw = await llamar_llm(prompt_eval, "", temperatura=0.1)

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

        raw = await llamar_llm(prompt, "", temperatura=0.2)
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
    await sesion_redis.agregar_mensaje(
        state["session_id"], "assistant", state["respuesta"]
    )
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
    if state.get("evaluacion_conversacion"):
        await mapa_observador.guardar_evaluacion_conversacion(
            state["session_id"], state.get("user_code","anonimo"),
            state["evaluacion_conversacion"]
        )
    return state
