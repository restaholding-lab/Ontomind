"""
main.py — API FastAPI de ONTOMIND
Endpoints para el chat y gestión de sesiones.
Flujo asíncrono: /chat devuelve job_id, /chat/status/{job_id} devuelve resultado.
"""
import os
import uuid
import time
import asyncio
import json as _json
import re as _re
import anthropic as _anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from graph import ontomind_graph
from memory import sesion_redis, mapa_observador

app = FastAPI(
    title       = "ONTOMIND API",
    description = "Motor conversacional ontológico — Echeverría + Pinotti",
    version     = "1.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ─── Job Storage (en memoria) ────────────────────────────
_jobs = {}
_job_timestamps = {}
JOB_TTL = 600  # 10 minutos

def _cleanup_old_jobs():
    """Elimina jobs con más de 10 minutos."""
    now = time.time()
    expired = [k for k, t in _job_timestamps.items() if now - t > JOB_TTL]
    for k in expired:
        _jobs.pop(k, None)
        _job_timestamps.pop(k, None)


# ─── Modelos ──────────────────────────────────────────────
class MensajeRequest(BaseModel):
    session_id: str
    mensaje:    str
    user_code:  str = "anonimo"

class SesionResponse(BaseModel):
    session_id: str

class EvaluarRequest(BaseModel):
    session_id: str
    user_code:  str = "anonimo"


# ─── Procesamiento en background ─────────────────────────
async def _procesar_chat(job_id: str, session_id: str,
                         mensaje: str, user_code: str):
    """Ejecuta el grafo completo en background y guarda el resultado."""
    try:
        # Guardar mensaje del usuario en Redis
        await sesion_redis.agregar_mensaje(session_id, "user", mensaje)
        turno = await sesion_redis.incrementar_turno(session_id)

        # Recuperar estado de sesión
        sesion = await sesion_redis.get(session_id)

        # Estado inicial del grafo
        estado_inicial = {
            "session_id":             session_id,
            "user_input":             mensaje,
            "turno_actual":           turno,
            "protocolo":              "normal",
            "reporte_actos":          {},
            "reporte_juicios":        {},
            "reporte_quiebre":        {},
            "reporte_victima":        {},
            "dictamen":               {},
            "historial":              {},
            "delta_observador":       "estable",
            "confianza_victima_acum": sesion.get("confianza_victima_acum", 0),
            "pregunta_dominio_hecha": sesion.get("pregunta_dominio_hecha", False),
            "nivel_riesgo":           "ninguno",
            "umbral_vigil":           0.70,
            "en_resguardo":           False,
            "ancora_previo":          False,
            "respuesta":              "",
            "evaluacion":             {},
            "evaluacion_conversacion": {},
            "turnos_sin_declaracion":  sesion.get("turnos_sin_declaracion", 0),
            "user_code":              user_code,
            "mensajes_historial":    []
        }

        # Ejecutar el grafo
        resultado = await ontomind_graph.ainvoke(estado_inicial)

        # Actualizar estado de sesión para el siguiente turno
        sesion["confianza_victima_acum"] = resultado.get("confianza_victima_acum", 0)
        sesion["pregunta_dominio_hecha"] = resultado.get("pregunta_dominio_hecha", False)
        eval_conv = resultado.get("evaluacion_conversacion", {})
        if eval_conv.get("declaracion_detectada"):
            sesion["turnos_sin_declaracion"] = 0
        else:
            sesion["turnos_sin_declaracion"] = sesion.get("turnos_sin_declaracion", 0) + 1
        sesion["protocolo"] = resultado.get("protocolo", "normal")
        await sesion_redis.set(session_id, sesion)

        # Guardar resultado
        _jobs[job_id] = {
            "status": "COMPLETED",
            "result": {
                "session_id": session_id,
                "respuesta":  resultado.get("respuesta", ""),
                "protocolo":  resultado.get("protocolo", "normal"),
                "turno":      turno
            }
        }
        print(f"[JOB] {job_id} completado — turno {turno}")

    except Exception as e:
        print(f"[JOB] {job_id} error: {e}")
        _jobs[job_id] = {
            "status": "FAILED",
            "error": str(e)[:200]
        }


# ─── Endpoints ────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "servicio": "ONTOMIND API v1.1"}


@app.post("/admin/dpo/guardar")
async def guardar_par_dpo(request: Request):
    """Guarda un par DPO desde el dashboard de etiquetado."""
    try:
        data = await request.json()
        import httpx as _httpx
        SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
        SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/pares_dpo",
                headers={
                    "apikey": SUPABASE_KEY,
                    "Authorization": "Bearer " + SUPABASE_KEY,
                    "Content-Type": "application/json",
                    "Prefer": "return=representation"
                },
                json={
                    "session_id":        data.get("session_id",""),
                    "turno":             data.get("turno", 1),
                    "user_code":         data.get("user_code","anonimo"),
                    "user_input":        data.get("user_input",""),
                    "perfil_detectado":  data.get("perfil_detectado",""),
                    "llave_maestra":     data.get("llave_maestra",""),
                    "protocolo":         data.get("protocolo","normal"),
                    "respuesta_rejected": data.get("respuesta_rejected",""),
                    "respuesta_chosen":   data.get("respuesta_chosen",""),
                    "supervisor":        data.get("supervisor","admin"),
                    "score_rejected":    data.get("score_rejected", 0),
                    "notas":             data.get("notas",""),
                    "categoria":         data.get("categoria","general"),
                }
            )
        if r.status_code in (200, 201):
            return {"ok": True, "id": r.json()[0].get("id") if r.json() else None}
        return {"ok": False, "error": r.text[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/sesion/nueva", response_model=SesionResponse)
async def nueva_sesion():
    """Crea una nueva sesión de conversación."""
    session_id = str(uuid.uuid4())
    return {"session_id": session_id}


@app.post("/chat")
async def chat(request: MensajeRequest):
    """
    Endpoint principal del chat ontológico — ASÍNCRONO.
    Devuelve un job_id inmediatamente. El cliente hace polling en /chat/status/{job_id}.
    """
    if not request.mensaje.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    _cleanup_old_jobs()

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "IN_PROGRESS"}
    _job_timestamps[job_id] = time.time()

    # Lanzar procesamiento en background
    asyncio.create_task(
        _procesar_chat(job_id, request.session_id,
                       request.mensaje, request.user_code)
    )

    return {"job_id": job_id, "status": "IN_PROGRESS"}


@app.get("/chat/status/{job_id}")
async def chat_status(job_id: str):
    """Consulta el estado de un job de chat."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.get("/sesion/{session_id}/historial")
async def get_historial(session_id: str):
    """Devuelve el historial de mensajes de una sesión."""
    mensajes = await sesion_redis.get_mensajes(session_id)
    return {"session_id": session_id, "mensajes": mensajes}


@app.get("/sesion/{session_id}/mapa")
async def get_mapa_observador(session_id: str):
    """Devuelve el Mapa del Observador del usuario."""
    mapa = await mapa_observador.get(session_id)
    return {"session_id": session_id, "mapa": mapa}


@app.get("/debug/env")
async def debug_env():
    import os
    key = os.getenv("OPENAI_API_KEY", "NOT_SET")
    return {
        "key_length": len(key),
        "key_start": key[:10] if len(key) > 10 else key,
        "key_end": key[-5:] if len(key) > 5 else key,
        "has_newline": chr(10) in key or chr(13) in key,
        "stripped_length": len(key.strip()),
    }


@app.get("/debug/supabase")
async def debug_supabase():
    import os, httpx
    url = os.getenv("SUPABASE_URL", "NOT_SET")
    key = os.getenv("SUPABASE_KEY", "NOT_SET")
    result = {
        "url_set": url != "NOT_SET",
        "url_start": url[:20] if len(url) > 20 else url,
        "key_set": key != "NOT_SET",
        "key_length": len(key),
    }
    if url != "NOT_SET" and key != "NOT_SET":
        try:
            r = await httpx.AsyncClient().get(
                url.strip() + "/rest/v1/mapa_observador?limit=1",
                headers={"apikey": key.strip(), "Authorization": "Bearer " + key.strip()},
                timeout=10
            )
            result["connection"] = r.status_code
            result["response"] = str(r.text[:100])
        except Exception as e:
            result["connection_error"] = str(e)
    return result


@app.get("/admin/tabla/{tabla}")
async def proxy_tabla(tabla: str, limit: int = 100, params: str = ""):
    """Proxy para que el dashboard lea Supabase a traves del backend."""
    import os, httpx
    url  = os.getenv("SUPABASE_URL","").strip()
    key  = os.getenv("SUPABASE_KEY","").strip()
    if not url or not key:
        return []
    tablas_permitidas = {"log_nodos", "mapa_observador", "alertas_vigil", "evaluaciones_conversacion", "usuarios", "pares_dpo"}
    if tabla not in tablas_permitidas:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Tabla no permitida")
    try:
        query = f"{url}/rest/v1/{tabla}?{params}&limit={limit}"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(query,
                headers={"apikey": key, "Authorization": "Bearer " + key})
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        return {"error": str(e)}

@app.patch("/admin/alertas_vigil/{alerta_id}")
async def marcar_revisado(alerta_id: int):
    import os, httpx
    url = os.getenv("SUPABASE_URL","").strip()
    key = os.getenv("SUPABASE_KEY","").strip()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.patch(
            f"{url}/rest/v1/alertas_vigil?id=eq.{alerta_id}",
            headers={"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"revisado": True})
    return {"ok": r.status_code in (200, 204)}


@app.post("/evaluar")
async def evaluar_sesion(request: EvaluarRequest):
    """
    Evaluación final de sesión — llamada única al cierre desde el dashboard.
    1. Recupera historial de Redis
    2. Recupera reportes de nodos de Supabase (últimos 20 turnos)
    3. Llama a Claude con PROMPT_EVALUADOR_CONVERSACION
    4. Guarda resultado en evaluaciones_conversacion (Supabase)
    5. Devuelve el dict de evaluación al frontend
    """
    import httpx as _httpx
    from prompts import PROMPT_EVALUADOR_CONVERSACION

    session_id = request.session_id
    user_code  = request.user_code or "anonimo"

    # ── 1. Historial de mensajes desde Redis ──────────────────────────────────
    try:
        mensajes     = await sesion_redis.get_mensajes(session_id)
        sesion       = await sesion_redis.get(session_id)
        total_turnos = sesion.get("turno_actual", len(mensajes) // 2)
    except Exception as e:
        print(f"[EVALUAR] Error Redis: {e}")
        mensajes, total_turnos = [], 0

    if not mensajes:
        return {"error": "Sin historial", "score_condiciones": 0, "arco_detectado": "estable"}

    historial_fmt = ""
    turno_n = 0
    for msg in mensajes:
        rol      = msg.get("rol", "")
        contenido = msg.get("contenido", "")
        if rol == "user":
            turno_n += 1
            historial_fmt += f"\n[Turno {turno_n}]\nUsuario: {contenido}\n"
        elif rol in ("agent", "assistant"):
            historial_fmt += f"ONTOMIND: {contenido}\n"

    # ── 2. Reportes acumulados de nodos desde Supabase ────────────────────────
    reportes_fmt = ""
    try:
        supa_url = os.getenv("SUPABASE_URL", "").strip()
        supa_key = os.getenv("SUPABASE_KEY", "").strip()
        if supa_url and supa_key:
            async with _httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{supa_url}/rest/v1/log_nodos"
                    f"?session_id=eq.{session_id}&order=turno.asc&limit=20",
                    headers={
                        "apikey":        supa_key,
                        "Authorization": f"Bearer {supa_key}",
                    },
                )
            if r.status_code == 200:
                for log in r.json():
                    t   = log.get("turno", "?")
                    vic = log.get("reporte_victima") or {}
                    dic = log.get("dictamen") or {}
                    qb  = log.get("reporte_quiebre") or {}
                    reportes_fmt += (
                        f"[T{t}] posicion={vic.get('posicion','?')} "
                        f"quiebre={qb.get('tipo_quiebre','?')} "
                        f"llave={dic.get('llave_maestra','?')} "
                        f"riesgo={log.get('nivel_riesgo','ninguno')} "
                        f"delta={log.get('delta_observador','estable')}\n"
                    )
    except Exception as e:
        print(f"[EVALUAR] Error Supabase reportes: {e}")

    if not reportes_fmt:
        reportes_fmt = "Sin reportes de nodos disponibles."

    # ── 3. Llamada a Claude ───────────────────────────────────────────────────
    raw = ""
    try:
        client_ant = _anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "").strip()
        )
        prompt_final = PROMPT_EVALUADOR_CONVERSACION.format(
            historial           = historial_fmt.strip(),
            reportes_acumulados = reportes_fmt.strip(),
        )
        response = client_ant.messages.create(
            model      = "claude-sonnet-4-5",
            max_tokens = 1200,
            messages   = [{"role": "user", "content": prompt_final}],
        )
        raw        = response.content[0].text.strip()
        raw        = _re.sub(r"^```json\s*", "", raw)
        raw        = _re.sub(r"\s*```$",     "", raw)
        evaluacion = _json.loads(raw)

    except _json.JSONDecodeError as e:
        print(f"[EVALUAR] JSON inválido: {e} | raw: {raw[:200]}")
        evaluacion = _fallback_evaluacion()
    except Exception as e:
        print(f"[EVALUAR] Error Claude: {e}")
        evaluacion = _fallback_evaluacion()

    # ── 4. Guardar en Supabase ────────────────────────────────────────────────
    try:
        evaluacion["total_turnos"] = total_turnos
        await mapa_observador.guardar_evaluacion_conversacion(
            session_id, user_code, evaluacion
        )
    except Exception as e:
        print(f"[EVALUAR] Error guardar: {e}")

    print(f"[EVALUAR] {session_id[:8]} score={evaluacion.get('score_condiciones',0)} arco={evaluacion.get('arco_detectado','?')}")
    return evaluacion


def _fallback_evaluacion() -> dict:
    """Evaluación neutra si Claude falla — el dashboard siempre muestra algo."""
    return {
        "posicion_inicial":       "mixto",
        "posicion_final":         "mixto",
        "arco_detectado":         "estable",
        "score_condiciones":      25,
        "posibilidad_nueva":      False,
        "creencia_en_movimiento": "no",
        "reconocimiento_quiebre": "ninguno",
        "declaracion_detectada":  False,
        "declaracion_texto":      "",
        "semilla_plantada":       "no",
        "llave_maestra_dominante":"—",
        "nivel_riesgo_max":       "ninguno",
        "recomendacion":          "",
        "_fallback":              True,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
