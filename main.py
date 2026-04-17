"""
main.py — API FastAPI de ONTOMIND
Endpoints para el chat y gestión de sesiones.
"""
import os
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from graph import ontomind_graph
from memory import sesion_redis, mapa_observador

app = FastAPI(
    title       = "ONTOMIND API",
    description = "Motor conversacional ontológico — Echeverría + Pinotti",
    version     = "1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)


# ─── Modelos ──────────────────────────────────────────────
class MensajeRequest(BaseModel):
    session_id: str
    mensaje:    str
    user_code:  str = "anonimo"

class MensajeResponse(BaseModel):
    session_id:  str
    respuesta:   str
    protocolo:   str
    turno:       int

class SesionResponse(BaseModel):
    session_id: str


# ─── Endpoints ────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "servicio": "ONTOMIND API v1.0"}


@app.post("/admin/dpo/guardar")
async def guardar_par_dpo(request: Request):
    """Guarda un par DPO desde el dashboard de etiquetado."""
    try:
        data = await request.json()
        import httpx as _httpx
        url  = SUPABASE_URL.strip()
        key  = SUPABASE_KEY.strip()
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{url}/rest/v1/pares_dpo",
                headers={
                    "apikey": key,
                    "Authorization": "Bearer " + key,
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


@app.post("/chat", response_model=MensajeResponse)
async def chat(request: MensajeRequest):
    """
    Endpoint principal del chat ontológico.
    Procesa el mensaje del usuario a través del grafo LangGraph.
    """
    if not request.mensaje.strip():
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    # Guardar mensaje del usuario en Redis
    await sesion_redis.agregar_mensaje(
        request.session_id, "user", request.mensaje
    )
    turno = await sesion_redis.incrementar_turno(request.session_id)

    # Recuperar estado de sesión
    sesion = await sesion_redis.get(request.session_id)

    # Estado inicial del grafo
    estado_inicial = {
        "session_id":             request.session_id,
        "user_input":             request.mensaje,
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
        "user_code":              request.user_code
    }

    # Ejecutar el grafo
    try:
        resultado = await ontomind_graph.ainvoke(estado_inicial)
    except Exception as e:
        print(f"[GRAPH ERROR] {e}")
        raise HTTPException(
            status_code=500,
            detail="Error procesando el mensaje. Intenta de nuevo."
        )

    # Actualizar estado de sesión para el siguiente turno
    sesion["confianza_victima_acum"] = resultado.get("confianza_victima_acum", 0)
    sesion["pregunta_dominio_hecha"] = resultado.get("pregunta_dominio_hecha", False)
    # Actualizar contador de turnos sin declaracion
    eval_conv = resultado.get("evaluacion_conversacion", {})
    if eval_conv.get("declaracion_detectada"):
        sesion["turnos_sin_declaracion"] = 0
    else:
        sesion["turnos_sin_declaracion"] = sesion.get("turnos_sin_declaracion", 0) + 1
    sesion["protocolo"]              = resultado.get("protocolo", "normal")
    await sesion_redis.set(request.session_id, sesion)

    return {
        "session_id": request.session_id,
        "respuesta":  resultado.get("respuesta", ""),
        "protocolo":  resultado.get("protocolo", "normal"),
        "turno":      turno
    }


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
    # Test actual connection
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
