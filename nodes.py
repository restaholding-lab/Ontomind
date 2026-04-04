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
                     PROMPT_P_VICTIMA, PROMPT_DISTINCIONES, PROMPT_MAESTRO)
from rag import recuperar_contexto, formatear_contexto
from memory import mapa_observador, sesion_redis

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
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


# ─── Llamada LLM genérica ────────────────────────────────
async def llamar_llm(system: str, user: str,
                     temperatura: float = 0.3) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type":  "application/json"
            },
            json={
                "model":       OPENAI_MODEL,
                "temperature": temperatura,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user}
                ]
            }
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()


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

    es_silencio = (
        len(tokens) < 5 or
        texto.lower() in tokens_silencio or
        len(texto) < 15
    )
    state["protocolo"] = "silencio" if es_silencio else "normal"
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

    state["nivel_riesgo"] = nivel
    return state


# ─── NODO 5: Prueba de Fuego (Doble Ciego VIGIL) ─────────
async def nodo_prueba_fuego(state: OntomindState) -> OntomindState:
    """
    Paso A: Pregunta de Dominio.
    Paso B: Evalúa respuesta. Activa ANCORA si hay colapso.
    """
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
async def nodo_maestro(state: OntomindState) -> OntomindState:
    """Sintetiza el dictamen en respuesta conversacional."""
    protocolo = state["protocolo"]

    # Protocolo VIGIL — respuesta directa sin LLM
    if protocolo == "vigil":
        state["respuesta"] = RESPUESTA_ANCORA
        return state

    dictamen = state.get("dictamen", {})
    delta    = state.get("delta_observador", "estable")

    contexto_maestro = (
        f"PROTOCOLO ACTIVO: {protocolo}\n"
        f"DELTA OBSERVADOR: {delta}\n"
        f"DICTAMEN DE [DISTINCIONES]:\n{json.dumps(dictamen, ensure_ascii=False)}\n\n"
        f"MENSAJE ORIGINAL DEL USUARIO:\n{state['user_input']}"
    )

    if delta == "transformacion":
        contexto_maestro += (
            "\n\nIMPORTANTE: El usuario muestra un cambio positivo "
            "respecto a sesiones anteriores. Celebra brevemente este "
            "cambio en una frase antes de la pregunta."
        )

    state["respuesta"] = await llamar_llm(
        PROMPT_MAESTRO,
        contexto_maestro,
        temperatura=0.6
    )
    return state


# ─── NODO 9: Actualizar Memoria ──────────────────────────
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
    return state
