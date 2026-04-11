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
                     PROMPT_EVALUADOR_CONVERSACION)
from rag import recuperar_contexto, formatear_contexto
from memory import mapa_observador, sesion_redis

OPENAI_API_KEY = "".join(os.getenv("OPENAI_API_KEY", "").split())
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
                     temperatura: float = 0.3) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + OPENAI_API_KEY.strip().replace(chr(10),"").replace(chr(13),""),
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

    # HARD-LOCK v2.1 — Umbral recalibrado para reducir falsos positivos
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

    contexto_maestro = (
        f"PROTOCOLO ACTIVO: {protocolo}\n"
        f"DELTA OBSERVADOR: {delta}\n"
        f"DICTAMEN DE [DISTINCIONES]:\n{json.dumps(dictamen, ensure_ascii=False)}\n\n"
        f"MENSAJE ORIGINAL DEL USUARIO:\n{state['user_input']}\n\n"
        f"{instruccion_preguntas}"
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

    # Inyectar Raiz Antropologica como marco permanente del Maestro
    prompt_maestro_enriquecido = PROMPT_MAESTRO + "\n\n" + CONTEXTO_RAIZ_ANTROPOLOGICA + instruccion_presencia
    state["respuesta"] = await llamar_llm(
        prompt_maestro_enriquecido,
        contexto_maestro,
        temperatura=0.6
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
        # Ultimas respuestas para detectar patron repetitivo
        mensajes_prev = await sesion_redis.get_mensajes(state["session_id"])
        resp_previas = [m["contenido"][:80] for m in mensajes_prev if m["rol"] == "assistant"][-2:]
        resp_actual = state.get("respuesta","")[:80]
        prev_txt = " | ".join(resp_previas) if resp_previas else "ninguna"

        prompt = (
            "Evalua esta respuesta de coaching ontologico.\n"
            "Usuario dijo: " + state.get("user_input", "") + "\n"
            "Respuesta del coach: " + state.get("respuesta", "") + "\n"
            "Respuestas anteriores (para detectar patron repetitivo): " + prev_txt + "\n\n"
            "METRICAS (responde SOLO numeros separados por coma en este orden):\n"
            "1. escucha_sombras 0-15: ¿Parafraseo directo sin 'Te escucho'/'Entiendo que'? ¿Sin apertura muleta?\n"
            "2. zarpazo_intercalado 0-10: ¿Inserto una pregunta de maximo 5 palabras EN MITAD de una frase (no al final)?\n"
            "3. espejo_crudo 0-10: ¿Tradujo un concepto blando (tranquilidad/dignidad/resignacion) a su verdad incomoda?\n"
            "4. hacia_declaracion 0-5: ¿Termino con afirmacion punzante O pregunta de declaracion (no siempre pregunta)?\n"
            "5. patron_repetitivo 0=no/1=si: ¿La estructura de inicio y fin es similar a las 2 respuestas anteriores?\n"
            "6. brevedad_impacto 0=no/1=si: ¿Hay alguna pregunta de menos de 6 palabras en el cuerpo del mensaje?\n"
            "7. arrogancia_intelectual 0=no/1=si: ¿Uso 'narrativa','saboteando','Te invito a reflexionar','Es posible que no te des cuenta','Hay una contradiccion central','Te escucho','Entiendo que','Me llega ese'?\n"
            "8. lenguaje_manual 0=no/1=si: ¿Uso frases de consejero como 'La excelencia surge de...','el apoyo mutuo es clave','podriais colaborar','las oportunidades de crecimiento','Te invito a considerar'?\n"
            "9. rotundidad_seca 0=no/1=si: ¿Nombra la emocion que el usuario NIEGA (ira, rabia, soberbia, miedo) sin suavizantes ni rodeos?\n"
            "10. zarpazo_identidad 0=no/1=si: ¿La pregunta intercalada ataca la IDENTIDAD del observador (quien eres tu ahi) en lugar del plan de accion (crees que eso es la solucion)?\n"
            "11. nota_breve: una frase\n"
            "Ejemplo: 12,8,7,4,0,1,0,0,1,1,Espejo crudo con zarpazo de identidad efectivo"
        )
        raw = await llamar_llm(prompt, "", temperatura=0.1)
        raw = raw.strip().replace("\n", " ")
        parts = raw.split(",", 5)
        def safe_int(v, default=0, max_val=10):
            try: return max(0, min(max_val, int(str(v).strip())))
            except: return default
        # Nuevas metricas: escucha_sombras, zarpazo_intercalado, espejo_crudo, hacia_declaracion,
        # patron_repetitivo, brevedad_impacto, arrogancia_intelectual, nota
        es  = safe_int(parts[0] if len(parts)>0 else 0, max_val=15)
        zi  = safe_int(parts[1] if len(parts)>1 else 0)
        ec  = safe_int(parts[2] if len(parts)>2 else 0)
        hd  = safe_int(parts[3] if len(parts)>3 else 0, max_val=5)
        rep = str(parts[4]).strip() == "1" if len(parts)>4 else False
        brv = str(parts[5]).strip() == "1" if len(parts)>5 else False
        arrog  = str(parts[6]).strip() == "1" if len(parts)>6 else False
        lm     = str(parts[7]).strip() == "1" if len(parts)>7 else False
        rots   = str(parts[8]).strip() == "1" if len(parts)>8 else False
        zid    = str(parts[9]).strip() == "1" if len(parts)>9 else False
        nota   = str(parts[10]).strip() if len(parts)>10 else "Sin nota"
        # Score: base + bonificaciones - penalizaciones
        base  = es + zi + ec + hd
        bonus = (10 if brv else 0) + (15 if rots else 0) + (5 if zid else 0)
        penal = (15 if rep else 0) + (20 if arrog else 0) + (20 if lm else 0)
        total = base + bonus - penal
        state["evaluacion"] = {
            "escucha_sombras":        es,
            "zarpazo_intercalado":    zi,
            "espejo_crudo":           ec,
            "hacia_declaracion":      hd,
            "brevedad_impacto":       brv,
            "patron_repetitivo":      rep,
            "arrogancia_intelectual": arrog,
            "lenguaje_manual":        lm,
            "rotundidad_seca":        rots,
            "zarpazo_identidad":      zid,
            "score_total":            max(0, total),
            "nota_evaluador":         nota
        }
        print(f"[EVALUADOR] Score: {max(0,total)}/55 | ZI:{zi} RS:{rots} ZID:{zid} LM:{lm} Arr:{arrog} | {nota}")
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
        raw = raw.strip().replace("\n", " ")
        parts = raw.split("|", 10)

        def sp(i, default=""): return str(parts[i]).strip() if len(parts) > i else default
        def si(i, default=0):
            try: return max(0, min(100, int(sp(i, str(default)))))
            except: return default

        datos = {
            "total_turnos":            state.get("turno_actual", 1),
            "posicion_inicial":        sp(0, "victima"),
            "posicion_final":          sp(1, "victima"),
            "arco_detectado":          sp(2, "estable"),
            "score_transformacion":    si(3, 10),
            "turno_quiebre":           si(4, 0),
            "declaracion_detectada":   sp(5, "no").lower() == "si",
            "declaracion_texto":       sp(6, ""),
            "llave_maestra_dominante": sp(7, ""),
            "nivel_riesgo_max":        sp(8, "ninguno"),
            "dictamen_conversacion":   sp(9, ""),
            "recomendacion":           sp(10, ""),
            "protocolo_dominante":     state.get("protocolo", "normal"),
        }

        state["evaluacion_conversacion"] = datos
        print(f"[EVAL-CONV] Score: {datos['score_transformacion']}/100 | Arco: {datos['arco_detectado']} | {datos['dictamen_conversacion'][:60]}")

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
