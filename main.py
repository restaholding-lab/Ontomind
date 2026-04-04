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
        "respuesta":              ""
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
