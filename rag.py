"""
rag.py — Recuperación RAG via REST puro (sin qdrant-client)
Genera embeddings con OpenAI y busca en Qdrant Cloud via httpx.
"""
import os
import httpx
from typing import Optional

QDRANT_URL     = os.getenv("QDRANT_URL", "").rstrip("/")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
OPENAI_API_KEY = "".join(os.getenv("OPENAI_API_KEY", "").split())
EMBED_MODEL    = "text-embedding-3-small"

# Mapa nodo → colección Qdrant
NODO_COLECCION = {
    "e_actos":               "ontomind_e_actos",
    "e_juicios":             "ontomind_e_juicios",
    "p_quiebre":             "ontomind_p_quiebre",
    "p_victima":             "ontomind_p_victima",
    "distinciones":          "ontomind_distinciones",
    "patron_conversacional": "ontomind_patron_conversacional",  # ← NUEVO
}

# Etiquetas legibles por fuente — para el prompt del Maestro
ETIQUETA_FUENTE = {
    "sesion_zoom_coaches":   "Sesión real de coaches (corpus base)",
    "sesion_zoom_video1":    "Sesión real — fragmento manual",
    "sesion_zoom_video2":    "Sesión real — fragmento manual",
    "sesion_zoom_video3":    "Sesión real — fragmento manual",
}

def _etiqueta_fuente(fuente: str) -> str:
    """Convierte el campo fuente en una etiqueta legible."""
    if fuente in ETIQUETA_FUENTE:
        return ETIQUETA_FUENTE[fuente]
    if fuente.startswith("sesion_zoom_"):
        return "Sesión real de coaches (video)"
    return fuente or "corpus"


async def embed_texto(texto: str) -> list[float]:
    """Genera embedding de un texto via OpenAI."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": "Bearer " + OPENAI_API_KEY.strip().replace(chr(10),"").replace(chr(13),""),
                "Content-Type":  "application/json"
            },
            json={"model": EMBED_MODEL, "input": [texto]}
        )
        r.raise_for_status()
        return r.json()["data"][0]["embedding"]


async def recuperar_contexto(
    nodo:    str,
    query:   str,
    top_k:   int = 4,
    filtro_autor: Optional[str] = None,
    solo_conversacional: bool = True
) -> list[dict]:
    """
    Recupera los fragmentos más relevantes del corpus para un nodo.

    Args:
        nodo: nombre del nodo ("e_actos", "e_juicios", "patron_conversacional", etc.)
        query: texto de búsqueda
        top_k: número de resultados
        filtro_autor: "echeverria" | "pinotti" | None (todos)
        solo_conversacional: si True, filtra chunks teóricos (tipo_tono != teorico)

    Returns:
        Lista de dicts con "texto", "autor", "score", "fuente", "dimension", "momento"
    """
    coleccion = NODO_COLECCION.get(nodo)
    if not coleccion:
        return []

    try:
        vector = await embed_texto(query)

        body = {
            "vector":          vector,
            "limit":           top_k,
            "with_payload":    True,
            "score_threshold": 0.35
        }

        filtros_must     = []
        filtros_must_not = []

        if filtro_autor:
            filtros_must.append({"key": "autor", "match": {"value": filtro_autor}})

        if solo_conversacional:
            filtros_must_not.append({"key": "tipo_tono", "match": {"value": "teorico"}})

        if filtros_must or filtros_must_not:
            body["filter"] = {}
            if filtros_must:
                body["filter"]["must"]     = filtros_must
            if filtros_must_not:
                body["filter"]["must_not"] = filtros_must_not

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{QDRANT_URL}/collections/{coleccion}/points/search",
                headers={
                    "api-key":      QDRANT_API_KEY,
                    "Content-Type": "application/json"
                },
                json=body
            )
            if r.status_code == 400 and "filter" in body:
                body_sin_filtro = {k: v for k, v in body.items() if k != "filter"}
                if filtro_autor:
                    body_sin_filtro["filter"] = {
                        "must": [{"key": "autor", "match": {"value": filtro_autor}}]
                    }
                r = await client.post(
                    f"{QDRANT_URL}/collections/{coleccion}/points/search",
                    headers={
                        "api-key":      QDRANT_API_KEY,
                        "Content-Type": "application/json"
                    },
                    json=body_sin_filtro
                )
            r.raise_for_status()
            resultados = r.json().get("result", [])

        fragmentos = []
        for res in resultados:
            payload = res["payload"]
            # Extraer fuente — campo varía según colección
            fuente_raw = (
                payload.get("fuente") or          # ontomind_patron_conversacional
                payload.get("filename") or         # colecciones antiguas
                "corpus"
            )
            # Extraer texto — campo varía según colección
            texto = (
                payload.get("dialogo") or          # patron_conversacional
                payload.get("text") or             # colecciones antiguas
                payload.get("coach_primera") or
                ""
            )[:600]

            fragmentos.append({
                "texto":     texto,
                "autor":     payload.get("autor", "coach"),
                "fuente":    fuente_raw,
                "fuente_label": _etiqueta_fuente(fuente_raw),
                "dimension": payload.get("dimension_principal", ""),
                "momento":   payload.get("momento_transformacion", ""),
                "patron":    payload.get("patron_coach", ""),
                "score":     res.get("score", 0)
            })

        return fragmentos

    except Exception as e:
        print(f"[RAG] Error en nodo {nodo}: {e}")
        return []


def formatear_contexto(fragmentos: list[dict]) -> str:
    """
    Formatea los fragmentos recuperados para incluir en el prompt del Maestro.
    Incluye la fuente para que el modelo sepa si viene de una sesión real o del corpus base.
    """
    if not fragmentos:
        return "Sin contexto recuperado del corpus."

    partes = []
    for i, f in enumerate(fragmentos, 1):
        # Cabecera con fuente identificada
        fuente_label = f.get("fuente_label") or f.get("fuente") or "corpus"
        dimension    = f.get("dimension", "")
        momento      = f.get("momento", "")
        patron       = f.get("patron", "")

        meta = f"[Fragmento {i} — {fuente_label}"
        if dimension:
            meta += f" | {dimension}"
        if momento:
            meta += f" | momento: {momento}"
        if patron:
            meta += f" | patrón: {patron}"
        meta += f" | score: {f['score']:.2f}]"

        partes.append(f"{meta}\n{f['texto']}")

    return "\n\n".join(partes)
