"""
memory.py — Memoria del sistema ONTOMIND v2
- Supabase: Mapa del Observador + Log de nodos por turno
- Redis: estado de sesión actual
"""
import os
import json
from datetime import datetime
from typing import Optional

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
REDIS_URL    = os.getenv("REDIS_URL", "")


class MapaObservador:
    def __init__(self):
        self.headers = {
            "apikey":        SUPABASE_KEY.strip(),
            "Authorization": f"Bearer {SUPABASE_KEY.strip()}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation"
        }

    async def _request(self, method, path, body=None):
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.request(
                method,
                f"{SUPABASE_URL.strip()}/rest/v1/{path}",
                headers=self.headers,
                json=body
            )
            if r.status_code in (200, 201):
                data = r.json()
                return data[0] if isinstance(data, list) and data else data
            return {}

    async def get(self, session_id):
        try:
            data = await self._request("GET",
                f"mapa_observador?session_id=eq.{session_id}&limit=1")
            return data or self._vacio(session_id)
        except Exception as e:
            print(f"[Supabase] Error get: {e}")
            return self._vacio(session_id)

    async def actualizar(self, session_id, datos):
        try:
            mapa = await self.get(session_id)
            historial = json.loads(mapa.get("historial_posiciones", "[]") or "[]")
            historial.append({
                "fecha":     datetime.utcnow().isoformat(),
                "posicion":  datos.get("posicion_victima", "desconocido"),
                "protocolo": datos.get("protocolo", "normal"),
                "delta":     datos.get("delta_observador", "estable")
            })
            historial = historial[-50:]
            await self._request("POST", "mapa_observador", {
                "session_id":           session_id,
                "ultima_posicion":      datos.get("posicion_victima"),
                "ultimo_quiebre":       datos.get("tipo_quiebre"),
                "ancora_activado":      datos.get("ancora_activado", False),
                "turnos_desde_ancora":  datos.get("turnos_desde_ancora", 0),
                "historial_posiciones": json.dumps(historial),
                "updated_at":           datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"[Supabase] Error actualizar: {e}")

    async def registrar_alerta_vigil(self, session_id, nivel, mensaje):
        try:
            await self._request("POST", "alertas_vigil", {
                "session_id": session_id,
                "nivel":      nivel,
                "mensaje":    mensaje,
                "timestamp":  datetime.utcnow().isoformat()
            })
        except Exception as e:
            print(f"[Supabase] Error alerta VIGIL: {e}")

    async def registrar_log_nodos(self, session_id, turno, estado):
        """
        Guarda el reporte completo de todos los nodos en cada turno.
        Esta es la fuente de aprendizaje para el equipo supervisor.
        """
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{SUPABASE_URL.strip()}/rest/v1/log_nodos",
                    headers={
                        "apikey":        SUPABASE_KEY.strip(),
                        "Authorization": f"Bearer {SUPABASE_KEY.strip()}",
                        "Content-Type":  "application/json"
                    },
                    json={
                        "session_id":      session_id,
                        "turno":           turno,
                        "timestamp":       datetime.utcnow().isoformat(),
                        "user_code":       estado.get("user_code", "anonimo"),
                        "user_input":      estado.get("user_input", ""),
                        "protocolo":       estado.get("protocolo", "normal"),
                        "reporte_actos":   estado.get("reporte_actos", {}),
                        "reporte_juicios": estado.get("reporte_juicios", {}),
                        "reporte_quiebre": estado.get("reporte_quiebre", {}),
                        "reporte_victima": estado.get("reporte_victima", {}),
                        "dictamen":        estado.get("dictamen", {}),
                        "delta_observador":estado.get("delta_observador", "estable"),
                        "respuesta":       estado.get("respuesta", ""),
                        "nivel_riesgo":    estado.get("nivel_riesgo", "ninguno")
                    }
                )
                if r.status_code not in (200, 201):
                    print(f"[Supabase] Error log_nodos: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"[Supabase] Error registrar_log_nodos: {e}")

    async def guardar_evaluacion_conversacion(self, session_id: str, user_code: str, datos: dict):
        """
        Guarda la evaluación longitudinal del arco conversacional.
        Marco: Condiciones de Transformación Sostenida (18 Abr 2026).
        """
        try:
            import httpx
            url = SUPABASE_URL.strip()
            key = SUPABASE_KEY.strip()
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{url}/rest/v1/evaluaciones_conversacion",
                    headers={
                        "apikey": key,
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates"
                    },
                    json={
                        "session_id":              session_id,
                        "user_code":               user_code,
                        "timestamp":               datetime.utcnow().isoformat(),
                        "total_turnos":            datos.get("total_turnos", 0),
                        "posicion_inicial":        datos.get("posicion_inicial", "victima"),
                        "posicion_final":          datos.get("posicion_final", "victima"),
                        "arco_detectado":          datos.get("arco_detectado", "estable"),
                        "score_condiciones":       datos.get("score_condiciones", 0),
                        "posibilidad_nueva":       datos.get("posibilidad_nueva", False),
                        "creencia_en_movimiento":  datos.get("creencia_en_movimiento", "no"),
                        "reconocimiento_quiebre":  datos.get("reconocimiento_quiebre", "ninguno"),
                        "semilla_plantada":        datos.get("semilla_plantada", ""),
                        "declaracion_detectada":   datos.get("declaracion_detectada", False),
                        "declaracion_texto":       datos.get("declaracion_texto", ""),
                        "llave_maestra_dominante": datos.get("llave_maestra_dominante", ""),
                        "protocolo_dominante":     datos.get("protocolo_dominante", "normal"),
                        "nivel_riesgo_max":        datos.get("nivel_riesgo_max", "ninguno"),
                        "recomendacion":           datos.get("recomendacion", ""),
                    }
                )
                if r.status_code not in (200, 201):
                    print(f"[Supabase] Error eval_conversacion: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"[Supabase] Error guardar_evaluacion_conversacion: {e}")

    async def guardar_evaluacion(self, session_id: str, turno: int, evaluacion: dict):
        """Actualiza el campo evaluacion en log_nodos para este turno."""
        try:
            import httpx, json
            url = SUPABASE_URL.strip()
            key = SUPABASE_KEY.strip()
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.patch(
                    f"{url}/rest/v1/log_nodos?session_id=eq.{session_id}&turno=eq.{turno}",
                    headers={
                        "apikey": key,
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json"
                    },
                    json={"evaluacion": evaluacion}
                )
                if r.status_code not in (200, 204):
                    print(f"[Supabase] Error guardar_evaluacion: {r.status_code}")
        except Exception as e:
            print(f"[Supabase] Error guardar_evaluacion: {e}")

    async def upsert_usuario(self, user_code: str, session_id: str):
        """Registra o actualiza el usuario por código."""
        try:
            import httpx
            url = SUPABASE_URL.strip()
            key = SUPABASE_KEY.strip()
            # Upsert usuario
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(
                    f"{url}/rest/v1/usuarios",
                    headers={
                        "apikey": key,
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates"
                    },
                    json={
                        "user_code":    user_code,
                        "ultima_sesion": datetime.utcnow().isoformat(),
                        "updated_at":   datetime.utcnow().isoformat()
                    }
                )
                # Incrementar total_sesiones
                await client.rpc(
                    f"{url}/rest/v1/rpc/incrementar_sesiones",
                    headers={
                        "apikey": key,
                        "Authorization": "Bearer " + key,
                        "Content-Type": "application/json"
                    },
                    json={"p_user_code": user_code}
                )
        except Exception as e:
            print(f"[Supabase] Error upsert_usuario: {e}")


    async def verificar_cadencia_dpo(self, user_code: str = "admin") -> dict:
        """
        Modificación 2 — Cadencia de fine-tuning iterativo.
        Comprueba cuántos pares DPO validados hay en Supabase.
        Alerta cuando se alcanza un múltiplo de 200 (umbral de entrenamiento).
        Devuelve dict con total, umbral_alcanzado y recomendacion.
        """
        try:
            import httpx
            url = SUPABASE_URL.strip()
            key = SUPABASE_KEY.strip()
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"{url}/rest/v1/pares_dpo?validado=eq.true&select=id",
                    headers={
                        "apikey": key,
                        "Authorization": "Bearer " + key,
                        "Prefer": "count=exact"
                    }
                )
                total = int(r.headers.get("content-range", "0/0").split("/")[-1])

            UMBRAL = 200
            ciclo_actual   = total // UMBRAL
            siguiente_hito = (ciclo_actual + 1) * UMBRAL
            faltan         = siguiente_hito - total
            umbral_alcanzado = (total > 0 and total % UMBRAL == 0)

            resultado = {
                "total_pares":        total,
                "ciclo_actual":       ciclo_actual,
                "siguiente_hito":     siguiente_hito,
                "faltan_para_hito":   faltan,
                "umbral_alcanzado":   umbral_alcanzado,
                "recomendacion":      (
                    f"LISTO PARA ENTRENAMIENTO — {total} pares validados. "
                    f"Ejecutar fine-tuning ciclo {ciclo_actual}."
                    if umbral_alcanzado else
                    f"Faltan {faltan} pares para el próximo entrenamiento "
                    f"(hito: {siguiente_hito})."
                )
            }

            if umbral_alcanzado:
                print(f"[DPO] ALERTA: {total} pares — umbral de entrenamiento alcanzado.")
            else:
                print(f"[DPO] {total} pares validados — faltan {faltan} para hito {siguiente_hito}.")

            return resultado

        except Exception as e:
            print(f"[DPO] Error verificar_cadencia_dpo: {e}")
            return {"total_pares": 0, "umbral_alcanzado": False, "recomendacion": f"Error: {e}"}

    def _vacio(self, session_id):
        return {
            "session_id":           session_id,
            "ultima_posicion":      "desconocido",
            "ultimo_quiebre":       None,
            "ancora_activado":      False,
            "turnos_desde_ancora":  999,
            "historial_posiciones": "[]"
        }


class SesionRedis:
    def __init__(self):
        self._cache = {}
        self._redis = None

    async def _get_redis(self):
        if self._redis: return self._redis
        if not REDIS_URL: return None
        try:
            import redis.asyncio as aioredis
            self._redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
            return self._redis
        except Exception:
            return None

    async def get(self, session_id):
        redis = await self._get_redis()
        if redis:
            try:
                data = await redis.get(f"sesion:{session_id}")
                return json.loads(data) if data else self._inicial()
            except Exception:
                pass
        return self._cache.get(session_id, self._inicial())

    async def set(self, session_id, estado, ttl=3600):
        redis = await self._get_redis()
        if redis:
            try:
                await redis.setex(f"sesion:{session_id}", ttl, json.dumps(estado))
                return
            except Exception:
                pass
        self._cache[session_id] = estado

    async def incrementar_turno(self, session_id):
        estado = await self.get(session_id)
        estado["turno_actual"] = estado.get("turno_actual", 0) + 1
        await self.set(session_id, estado)
        return estado["turno_actual"]

    async def agregar_mensaje(self, session_id, rol, contenido):
        estado = await self.get(session_id)
        msgs = estado.get("mensajes", [])
        msgs.append({"rol": rol, "contenido": contenido})
        estado["mensajes"] = msgs[-20:]
        await self.set(session_id, estado)

    async def get_mensajes(self, session_id):
        estado = await self.get(session_id)
        return estado.get("mensajes", [])

    def _inicial(self):
        return {
            "turno_actual": 0,
            "mensajes": [],
            "confianza_victima_acum": 0,
            "pregunta_dominio_hecha": False,
            "ancora_activado": False,
            "protocolo": "normal"
        }


mapa_observador = MapaObservador()
sesion_redis    = SesionRedis()