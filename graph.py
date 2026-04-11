"""
graph.py — Definición del grafo LangGraph ONTOMIND
Orquesta los 9 nodos con la lógica de routing completa.
"""
from typing import Literal
from langgraph.graph import StateGraph, END

from nodes import (
    OntomindState,
    nodo_calibrar_escucha,
    nodo_clasificar_input,
    nodo_detectores,
    nodo_incoherencia,
    nodo_triple_filtro_vigil,
    nodo_prueba_fuego,
    nodo_distinciones,
    nodo_historial,
    nodo_maestro,
    nodo_evaluador,
    nodo_evaluador_conversacion,
    nodo_actualizar_memoria,
)


# ─── Routers (funciones de decisión) ─────────────────────

def router_silencio(state: OntomindState) -> Literal["detectores", "distinciones"]:
    """Si hay silencio, salta los detectores y va directo al Incisor."""
    return "distinciones" if state["protocolo"] == "silencio" else "detectores"


def router_riesgo(state: OntomindState) -> Literal["prueba_fuego", "distinciones"]:
    """
    VIGIL v2.1 — Routing condicional:
    - CRITICO: siempre a prueba_fuego (ANCORA)
    - ALTO con Llave Maestra activa: ceder al Maestro via distinciones (coaching, no crisis)
    - ALTO sin Llave Maestra: a prueba_fuego
    - LATENTE/NINGUNO: a distinciones
    """
    nivel = state["nivel_riesgo"]
    llave = state.get("dictamen", {}).get("llave_maestra", "")
    if nivel == "critico":
        return "prueba_fuego"
    if nivel == "alto" and llave:
        # Hay llave maestra activa: es coaching, no crisis psiquiatrica
        # VIGIL ya alertó al supervisor, el Maestro continúa
        return "distinciones"
    if nivel == "alto":
        return "prueba_fuego"
    return "distinciones"


def router_prueba_fuego(state: OntomindState) -> Literal["maestro", "distinciones"]:
    """Después de la Prueba de Fuego: si es vigil va al Maestro directo."""
    return "maestro" if state["protocolo"] == "vigil" else "distinciones"


# ─── Construcción del grafo ───────────────────────────────

def construir_grafo() -> StateGraph:
    grafo = StateGraph(OntomindState)

    # Registrar nodos
    grafo.add_node("calibrar_escucha",  nodo_calibrar_escucha)
    grafo.add_node("clasificar_input",  nodo_clasificar_input)
    grafo.add_node("detectores",        nodo_detectores)
    grafo.add_node("incoherencia",      nodo_incoherencia)
    grafo.add_node("triple_filtro",     nodo_triple_filtro_vigil)
    grafo.add_node("prueba_fuego",      nodo_prueba_fuego)
    grafo.add_node("distinciones",      nodo_distinciones)
    grafo.add_node("consultar_historial",         nodo_historial)
    grafo.add_node("maestro",           nodo_maestro)
    grafo.add_node("actualizar_memoria",nodo_actualizar_memoria)

    # Flujo principal
    grafo.set_entry_point("calibrar_escucha")
    grafo.add_edge("calibrar_escucha", "clasificar_input")

    # Bifurcación: silencio → distinciones | normal → detectores
    grafo.add_conditional_edges(
        "clasificar_input",
        router_silencio,
        {"detectores": "detectores", "distinciones": "distinciones"}
    )

    # Flujo normal: detectores → incoherencia → triple_filtro
    grafo.add_edge("detectores",  "incoherencia")
    grafo.add_edge("incoherencia", "triple_filtro")

    # Bifurcación: riesgo alto → prueba_fuego | normal → distinciones
    grafo.add_conditional_edges(
        "triple_filtro",
        router_riesgo,
        {"prueba_fuego": "prueba_fuego", "distinciones": "distinciones"}
    )

    # Bifurcación: protocolo vigil → maestro | normal → distinciones
    grafo.add_conditional_edges(
        "prueba_fuego",
        router_prueba_fueba,
        {"maestro": "maestro", "distinciones": "distinciones"}
    )

    # Flujo final
    grafo.add_edge("distinciones", "consultar_historial")
    grafo.add_edge("consultar_historial", "maestro")
    grafo.add_node("evaluador",          nodo_evaluador)
    grafo.add_node("eval_conversacion",  nodo_evaluador_conversacion)
    grafo.add_edge("maestro",            "evaluador")
    grafo.add_edge("evaluador",          "eval_conversacion")
    grafo.add_edge("eval_conversacion",  "actualizar_memoria")
    grafo.add_edge("actualizar_memoria", END)

    return grafo.compile()


# Typo fix para el router
def router_prueba_fueba(state: OntomindState) -> Literal["maestro", "distinciones"]:
    return "maestro" if state["protocolo"] == "vigil" else "distinciones"


# Instancia compilada del grafo
ontomind_graph = construir_grafo()
