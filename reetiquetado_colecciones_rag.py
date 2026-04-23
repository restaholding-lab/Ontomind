"""
reetiquetado_colecciones_rag.py
Actualiza los payloads de las 5 colecciones teóricas de Qdrant
con los nuevos campos del marco de transformación sostenida.
NO re-embeda — solo actualiza metadatos.

Ejecutar desde C:\\Ontomind-Backend:
  $env:QDRANT_API_KEY="..."
  python reetiquetado_colecciones_rag.py
"""

import os, time
from qdrant_client import QdrantClient
from qdrant_client.models import PayloadSelector, PointIdsList

QDRANT_URL     = "https://e7b48f2b-8981-4ec6-9371-6bdf21bd278c.eu-west-1-0.aws.cloud.qdrant.io"
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")

q = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

# ─── REGLAS DE ETIQUETADO POR COLECCIÓN ──────────────────────────────────────
#
# Mapa: coleccion → dimensión base + reglas por tipo_tono
#
# Las dimensiones del nuevo marco:
#   apertura_posibilidad, escucha_activa, emocion_indicador,
#   incomodidad_calibrada, lenguaje_devuelto, acompañamiento, compromiso_emergente

REGLAS = {
    "ontomind_e_actos": {
        "dimension_base":    "compromiso_emergente",
        "momento_base":      "declaracion",
        "patron_base":       "devolucion",
        "intensidad_base":   "media",
        "fuente_label":      "corpus_teorico_actos_habla",
        "por_tono": {
            "zarpazo":           ("incomodidad_calibrada", "quiebre",     "acorralamiento", "alta"),
            "pregunta_2o_orden": ("apertura_posibilidad",  "exploracion", "apertura",       "media"),
            "conversacional":    ("compromiso_emergente",  "declaracion", "devolucion",     "suave"),
            "validacion":        ("escucha_activa",         "exploracion", "devolucion",     "suave"),
        }
    },
    "ontomind_e_juicios": {
        "dimension_base":    "lenguaje_devuelto",
        "momento_base":      "quiebre",
        "patron_base":       "espejo_lenguaje",
        "intensidad_base":   "alta",
        "fuente_label":      "corpus_teorico_juicios",
        "por_tono": {
            "zarpazo":           ("incomodidad_calibrada", "quiebre",     "acorralamiento", "alta"),
            "pregunta_2o_orden": ("lenguaje_devuelto",     "quiebre",     "espejo_lenguaje","alta"),
            "conversacional":    ("acompañamiento",         "exploracion", "sostenimiento",  "media"),
            "validacion":        ("escucha_activa",         "exploracion", "devolucion",     "suave"),
            "teorico":           ("lenguaje_devuelto",     "exploracion", "espejo_lenguaje","suave"),
        }
    },
    "ontomind_p_quiebre": {
        "dimension_base":    "incomodidad_calibrada",
        "momento_base":      "quiebre",
        "patron_base":       "acorralamiento",
        "intensidad_base":   "alta",
        "fuente_label":      "corpus_teorico_quiebre",
        "por_tono": {
            "zarpazo":           ("incomodidad_calibrada", "quiebre",     "acorralamiento", "alta"),
            "pregunta_2o_orden": ("apertura_posibilidad",  "quiebre",     "apertura",       "media"),
            "conversacional":    ("acompañamiento",         "exploracion", "sostenimiento",  "media"),
            "validacion":        ("escucha_activa",         "exploracion", "devolucion",     "suave"),
            "teorico":           ("incomodidad_calibrada", "exploracion", "espejo_lenguaje","suave"),
        }
    },
    "ontomind_p_victima": {
        "dimension_base":    "apertura_posibilidad",
        "momento_base":      "quiebre",
        "patron_base":       "espejo_lenguaje",
        "intensidad_base":   "alta",
        "fuente_label":      "corpus_teorico_victima",
        "por_tono": {
            "zarpazo":           ("incomodidad_calibrada", "quiebre",     "acorralamiento", "alta"),
            "pregunta_2o_orden": ("apertura_posibilidad",  "quiebre",     "apertura",       "media"),
            "conversacional":    ("acompañamiento",         "exploracion", "sostenimiento",  "media"),
            "validacion":        ("escucha_activa",         "exploracion", "devolucion",     "suave"),
            "teorico":           ("apertura_posibilidad",  "exploracion", "espejo_lenguaje","suave"),
        }
    },
    "ontomind_distinciones": {
        "dimension_base":    "apertura_posibilidad",
        "momento_base":      "exploracion",
        "patron_base":       "apertura",
        "intensidad_base":   "media",
        "fuente_label":      "corpus_teorico_distinciones",
        "por_tono": {
            "zarpazo":           ("incomodidad_calibrada", "quiebre",     "acorralamiento", "alta"),
            "pregunta_2o_orden": ("apertura_posibilidad",  "exploracion", "apertura",       "media"),
            "conversacional":    ("acompañamiento",         "siembra",     "sostenimiento",  "suave"),
            "validacion":        ("escucha_activa",         "exploracion", "devolucion",     "suave"),
            "teorico":           ("apertura_posibilidad",  "exploracion", "apertura",       "suave"),
        }
    },
}


def calcular_etiquetas(payload: dict, reglas: dict) -> dict:
    """Calcula los nuevos campos según tipo_tono del fragmento."""
    tono = payload.get("tipo_tono", "conversacional")
    por_tono = reglas.get("por_tono", {})

    if tono in por_tono:
        dim, momento, patron, intens = por_tono[tono]
    else:
        dim     = reglas["dimension_base"]
        momento = reglas["momento_base"]
        patron  = reglas["patron_base"]
        intens  = reglas["intensidad_base"]

    return {
        "dimension_principal":    dim,
        "momento_transformacion": momento,
        "patron_coach":           patron,
        "intensidad_incomodidad": intens,
        "fuente":                 reglas["fuente_label"],
    }


def reetiquetart_coleccion(nombre: str, reglas: dict) -> tuple[int, int]:
    """
    Actualiza todos los vectores de una colección con los nuevos campos.
    Devuelve (actualizados, total).
    """
    print(f"\n[{nombre}]")
    info = q.get_collection(nombre)
    total = info.points_count
    print(f"  Vectores: {total}")

    actualizados = 0
    offset = None
    BATCH = 100

    while True:
        resultados, next_offset = q.scroll(
            collection_name=nombre,
            limit=BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=False
        )

        if not resultados:
            break

        for punto in resultados:
            nuevos_campos = calcular_etiquetas(punto.payload, reglas)

            # Solo actualizar si faltan los campos (evita trabajo redundante)
            if "dimension_principal" not in punto.payload:
                q.set_payload(
                    collection_name=nombre,
                    payload=nuevos_campos,
                    points=[punto.id]
                )
                actualizados += 1
            else:
                # Siempre actualizar fuente para asegurar coherencia
                q.set_payload(
                    collection_name=nombre,
                    payload={"fuente": reglas["fuente_label"]},
                    points=[punto.id]
                )
                actualizados += 1

        offset = next_offset
        print(f"  Progreso: {actualizados}/{total}", end="\r")

        if next_offset is None:
            break
        time.sleep(0.05)

    print(f"  Completado: {actualizados}/{total}          ")
    return actualizados, total


if __name__ == "__main__":
    if not QDRANT_API_KEY:
        print("ERR Falta QDRANT_API_KEY")
        exit(1)

    print("=== RE-ETIQUETADO COLECCIONES RAG ONTOMIND ===")
    print("Marco: Condiciones de Transformacion Sostenida")
    print("Campos nuevos: dimension_principal, momento_transformacion,")
    print("               patron_coach, intensidad_incomodidad, fuente")
    print()

    total_ok = 0
    total_all = 0

    for coleccion, reglas in REGLAS.items():
        ok, total = reetiquetart_coleccion(coleccion, reglas)
        total_ok  += ok
        total_all += total

    print(f"\n{'='*50}")
    print(f"TOTAL ACTUALIZADOS: {total_ok}/{total_all}")
    print()
    print("Verificando campos en una muestra...")
    for col in REGLAS:
        r, _ = q.scroll(col, limit=1, with_payload=True, with_vectors=False)
        if r:
            p = r[0].payload
            dim  = p.get("dimension_principal", "FALTA")
            fnt  = p.get("fuente", "FALTA")
            print(f"  {col}: dimension={dim} | fuente={fnt}")
