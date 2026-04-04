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
    "e_actos":      "ontomind_e_actos",
    "e_juicios":    "ontomind_e_juicios",
    "p_quiebre":    "ontomind_p_quiebre",
    "p_victima":    "ontomind_p_victima",
    "distinciones": "ontomind_distinciones",
}


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
    filtro_autor: Optional[str] = None
) -> list[dict]:
    """
    Recupera los fragmentos más relevantes del corpus para un nodo.
    
    Args:
        nodo: nombre del nodo ("e_actos", "e_juicios", etc.)
        query: texto de búsqueda
        top_k: número de resultados
        filtro_autor: "echeverria" | "pinotti" | None (todos)
    
    Returns:
        Lista de dicts con "texto", "autor", "score", "fuente"
    """
    coleccion = NODO_COLECCION.get(nodo)
    if not coleccion:
        return []

    try:
        vector = await embed_texto(query)

        body = {
            "vector":       vector,
            "limit":        top_k,
            "with_payload": True,
            "score_threshold": 0.35
        }

        # Filtro opcional por autor
        if filtro_autor:
            body["filter"] = {
                "must": [{"key": "autor", "match": {"value": filtro_autor}}]
            }

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{QDRANT_URL}/collections/{coleccion}/points/search",
                headers={
                    "api-key":      QDRANT_API_KEY,
                    "Content-Type": "application/json"
                },
                json=body
            )
            r.raise_for_status()
            resultados = r.json().get("result", [])

        return [
            {
                "texto":  res["payload"].get("text", "")[:600],
                "autor":  res["payload"].get("autor", "desconocido"),
                "fuente": res["payload"].get("filename", ""),
                "score":  res.get("score", 0)
            }
            for res in resultados
        ]

    except Exception as e:
        print(f"[RAG] Error en nodo {nodo}: {e}")
        return []


def formatear_contexto(fragmentos: list[dict]) -> str:
    """Formatea los fragmentos recuperados para incluir en el prompt."""
    if not fragmentos:
        return "Sin contexto recuperado del corpus."
    
    partes = []
    for i, f in enumerate(fragmentos, 1):
        partes.append(
            f"[Fragmento {i} — {f['autor'].upper()} | score: {f['score']:.2f}]\n"
            f"{f['texto']}"
        )
    return "\n\n".join(partes)
