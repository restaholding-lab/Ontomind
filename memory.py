"""
memory.py — Memoria del sistema ONTOMIND
- Supabase: Mapa del Observador (persistente entre sesiones)
- Redis: estado de sesión actual (temporal)
"""
import os
import json
from datetime import datetime
from typing import Optional

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
REDIS_URL    = os.getenv("REDIS_URL", "")


# ─── SUPABASE — Mapa del Observador ──────────────────────
class MapaObservador:
    """
    Perfil dinámico del usuario en Supabase.
    El presente siempre manda sobre el historial.
    El historial es solo contraste, nunca sesgo.
    """

    def __init__(self):
        self.headers = {
            "apikey":        SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation"
        }

    async def _request(self, method: str, path: str, body=None) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.request(
                method,
                f"{SUPABASE_URL}/rest/v1/{path}",
                headers=self.headers,
                json=body
            )
            if r.status_code in (200, 201):
                data = r.json()
                return data[0] if isinstance(data, list) and data else data
            return {}

    async def get(self, session_id: str) -> dict:
        """Recupera el mapa del observador de un usuario."""
        try:
            data = await self._request(
                "GET",
                f"mapa_observador?session_id=eq.{session_id}&limit=1"
            )
            return data or self._mapa_vacio(session_id)
        except Exception as e:
            print(f"[Supabase] Error get: {e}")
            return self._mapa_vacio(session_id)

    async def actualizar(self, session_id: str, datos_sesion: dict) -> bool:
        """
        Actualiza el mapa del observador con los datos de la sesión actual.
        El presente es soberano — acumula sin sobrescribir el historial.
        """
        try:
            mapa_actual = await self.get(session_id)
            
            # Acumular historial de posiciones
            historial = mapa_actual.get("historial_posiciones", [])
            historial.append({
                "fecha":     datetime.utcnow().isoformat(),
                "posicion":  datos_sesion.get("posicion_victima", "desconocido"),
                "protocolo": datos_sesion.get("protocolo", "normal"),
                "delta":     datos_sesion.get("delta_observador", "estable")
            })
            # Mantener solo los últimos 50 registros
            historial = historial[-50:]

            cuerpo = {
                "session_id":           session_id,
                "ultima_posicion":      datos_sesion.get("posicion_victima"),
                "ultimo_quiebre":       datos_sesion.get("tipo_quiebre"),
                "ancora_activado":      datos_sesion.get("ancora_activado", False),
                "turnos_desde_ancora":  datos_sesion.get("turnos_desde_ancora", 0),
                "historial_posiciones": json.dumps(historial),
                "updated_at":           datetime.utcnow().isoformat()
            }

            # Upsert — crea o actualiza
            await self._request(
                "POST",
                "mapa_observador",
                cuerpo
            )
            return True
        except Exception as e:
            print(f"[Supabase] Error actualizar: {e}")
            return False

    async def registrar_alerta_vigil(self, session_id: str,
                                      nivel: str, mensaje: str) -> None:
        """Log silencioso de alerta VIGIL para supervisores."""
        try:
            await self._request("POST", "alertas_vigil", {
                "session_id": session_id,
                "nivel":      nivel,
                "mensaje":    mensaje,
                "timestamp":  datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"[Supabase] Error alerta VIGIL: {e}")

    def _mapa_vacio(self, session_id: str) -> dict:
        return {
            "session_id":           session_id,
            "ultima_posicion":      "desconocido",
            "ultimo_quiebre":       None,
            "ancora_activado":      False,
            "turnos_desde_ancora":  999,
            "historial_posiciones": "[]"
        }


# ─── REDIS — Estado de sesión ─────────────────────────────
class SesionRedis:
    """
    Estado temporal de la conversación actual.
    Se borra cuando la sesión termina.
    """

    def __init__(self):
        self._cache: dict = {}  # fallback en memoria si Redis no está disponible
        self._redis = None

    async def _get_redis(self):
        if self._redis:
            return self._redis
        if not REDIS_URL:
            return None
        try:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
            return self._redis
        except Exception:
            return None

    async def get(self, session_id: str) -> dict:
        redis = await self._get_redis()
        if redis:
            try:
                data = await redis.get(f"sesion:{session_id}")
                return json.loads(data) if data else self._sesion_inicial()
            except Exception:
                pass
        return self._cache.get(session_id, self._sesion_inicial())

    async def set(self, session_id: str, estado: dict,
                  ttl_segundos: int = 3600) -> None:
        redis = await self._get_redis()
        if redis:
            try:
                await redis.setex(
                    f"sesion:{session_id}",
                    ttl_segundos,
                    json.dumps(estado)
                )
                return
            except Exception:
                pass
        self._cache[session_id] = estado

    async def incrementar_turno(self, session_id: str) -> int:
        estado = await self.get(session_id)
        estado["turno_actual"] = estado.get("turno_actual", 0) + 1
        await self.set(session_id, estado)
        return estado["turno_actual"]

    async def agregar_mensaje(self, session_id: str,
                               rol: str, contenido: str) -> None:
        estado = await self.get(session_id)
        mensajes = estado.get("mensajes", [])
        mensajes.append({"rol": rol, "contenido": contenido})
        mensajes = mensajes[-20:]  # últimos 20 mensajes
        estado["mensajes"] = mensajes
        await self.set(session_id, estado)

    async def get_mensajes(self, session_id: str) -> list:
        estado = await self.get(session_id)
        return estado.get("mensajes", [])

    def _sesion_inicial(self) -> dict:
        return {
            "turno_actual":           0,
            "mensajes":               [],
            "confianza_victima_acum": 0,
            "pregunta_dominio_hecha": False,
            "ancora_activado":        False,
            "protocolo":              "normal"
        }


# Instancias globales
mapa_observador = MapaObservador()
sesion_redis    = SesionRedis()
