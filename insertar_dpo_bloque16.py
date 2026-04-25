"""
insertar_dpo_bloque16.py
Inserta los pares DPO 151-160 en Supabase.
Foco: distinciones avanzadas.
      Responsabilidad vs Culpa, Amor como verbo, Poder vs Fuerza,
      Silencio activo, Libertad vs Consentimiento.
      La vigilancia activa como práctica sostenida, no como esfuerzo heroico.

Ejecutar desde C:\\Ontomind-Backend:
  $env:SUPABASE_URL="https://zupmvaiqwzgickbygpnv.supabase.co"
  $env:SUPABASE_KEY="tu_key"
  python insertar_dpo_bloque16.py
"""

import os, httpx

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://zupmvaiqwzgickbygpnv.supabase.co").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

HEADERS = {
    "apikey":        SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation"
}

PARES = [
    # ── CASO 151 — Responsabilidad vs Culpa ──────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              151,
        "user_input":         "Todo esto es culpa mía. Debería haberlo visto antes. Soy un idiota.",
        "perfil_detectado":   "dolor_agudo",
        "llave_maestra":      "Fundamentación de Juicios",
        "categoria":          "distinciones",
        "respuesta_rejected": "No seas tan duro contigo mismo. Todos cometemos errores y lo importante es aprender de ellos. ¿Qué podrías hacer diferente a partir de ahora?",
        "respuesta_chosen":   "Culpa y responsabilidad no son lo mismo. La culpa te paraliza. La responsabilidad te da poder de acción. ¿Qué parte de esto puedes adueñarte para hacer algo con ello?",
        "supervisor":         "admin",
        "score_rejected":     3,
        "notas":              "DISTINCIÓN: Responsabilidad vs Culpa. Rejected: rebate el autojuicio + advisory. Chosen: introduce la distinción como herramienta de acción — no para absolver sino para mover.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 152 — La vigilancia activa — la voz que vuelve ──────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              152,
        "user_input":         "Estaba yendo bien pero esta semana esa voz volvió. La que me dice que no vale la pena, que para qué si total...",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Declaración de No-Posibilidad",
        "categoria":          "distinciones",
        "respuesta_rejected": "Es normal que esa voz aparezca. El camino del cambio no es lineal. Lo importante es no dejar que te detenga. ¿Qué te ayuda a no hacerle caso?",
        "respuesta_chosen":   "Esa voz no es nueva. Es la misma de siempre — la que apareció antes de empezar. ¿Qué tiene de diferente escucharla ahora que ya sabes lo que cuesta ignorarla?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "VIGILANCIA ACTIVA. Rejected: normaliza + advisory + busca herramienta. Chosen: devuelve que la voz no es una señal nueva — es la inercia conocida. Pregunta qué ha cambiado en el que la escucha.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 153 — Amor como verbo ────────────────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              153,
        "user_input":         "Le quiero pero ya no sé si estoy enamorado. El amor se fue yendo solo.",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Fundamentación de Juicios",
        "categoria":          "distinciones",
        "respuesta_rejected": "El enamoramiento inicial cambia con el tiempo en todas las relaciones. Es normal que evolucione hacia algo más tranquilo. ¿Qué es lo que sientes exactamente cuando estás con él o con ella?",
        "respuesta_chosen":   "—¿Y si el amor no se va sino que deja de practicarse? ¿Cuándo fue la última vez que hiciste algo concreto desde ese amor?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "DISTINCIÓN: Amor como verbo. Rejected: normaliza la evolución + pregunta al sentimiento. Chosen: introduce amor como práctica activa, no como estado que se tiene o no — desafía la pasividad.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 154 — Poder vs Fuerza ────────────────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              154,
        "user_input":         "Necesito que me respeten en el trabajo. Nadie me toma en serio por mucho que me esfuerce.",
        "perfil_detectado":   "victima_estancada",
        "llave_maestra":      "Brecha de Efectividad",
        "categoria":          "distinciones",
        "respuesta_rejected": "El respeto se gana con el tiempo y con la consistencia. ¿Has hablado directamente con tu equipo o tu jefe sobre cómo te sientes?",
        "respuesta_chosen":   "El esfuerzo genera fuerza. El respeto viene del poder — de lo que declaras y cumples. ¿Qué has declarado tú en ese equipo que luego hayas cumplido?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "DISTINCIÓN: Poder vs Fuerza. Rejected: advisory de comunicación. Chosen: introduce la distinción — el respeto no viene del esfuerzo (fuerza) sino de la coherencia entre declaración y acción (poder).",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 155 — Silencio activo ────────────────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              155,
        "user_input":         "Cuando discutimos, él grita y yo me callo. Luego me arrepiento de no haber dicho nada.",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Autoridad Ontológica",
        "categoria":          "distinciones",
        "respuesta_rejected": "Callarse en el momento del conflicto puede ser una respuesta al miedo o a querer evitar la escalada. ¿Por qué crees que te quedas en silencio?",
        "respuesta_chosen":   "Hay dos tipos de silencio. El que te protege y el que te traiciona. ¿Cuál es el tuyo en ese momento?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "DISTINCIÓN: Silencio activo. Rejected: interpreta + pregunta al origen. Chosen: introduce la distinción sin diagnosticar — el usuario decide cuál de los dos es el suyo.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 156 — Libertad vs Consentimiento ────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              156,
        "user_input":         "Hago lo que se espera de mí. En el trabajo, en casa. Supongo que así es la vida.",
        "perfil_detectado":   "victima_estancada",
        "llave_maestra":      "Declaración de No-Posibilidad",
        "categoria":          "distinciones",
        "respuesta_rejected": "Cumplir con las expectativas de los demás puede ser agotador. ¿Hay algo que harías diferente si no tuvieras que cumplir con lo que se espera de ti?",
        "respuesta_chosen":   "Lo que describes se llama consentimiento — actuar desde lo que otros esperan sin haberlo elegido. La libertad no es hacer lo que quieres. Es reconocerte en lo que eliges. ¿Qué has elegido tú últimamente?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "DISTINCIÓN: Libertad vs Consentimiento. Rejected: valida el agotamiento + pregunta hipotética. Chosen: nombra la distinción directamente + pregunta que devuelve la autoría.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 157 — La vigilancia: la inercia cultural ─────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              157,
        "user_input":         "Mi familia no entiende los cambios que estoy haciendo. Dicen que me he vuelto raro.",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Fundamentación de Juicios",
        "categoria":          "distinciones",
        "respuesta_rejected": "El entorno familiar puede resistirse al cambio cuando ven que alguien evoluciona. ¿Cómo te afecta lo que dicen?",
        "respuesta_chosen":   "La inercia cultural actúa desde fuera igual que la voz interior actúa desde dentro. Ambas dicen lo mismo: vuelve al sitio donde te conocíamos. ¿Qué necesitas para seguir sin que eso te pare?",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "VIGILANCIA ACTIVA — inercia cultural externa. Chosen: nombra el mecanismo (inercia cultural) + lo conecta con la voz interior — son la misma fuerza desde fuera y desde dentro.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 158 — Sabiduría vs Conocimiento ─────────────────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              158,
        "user_input":         "He leído mucho sobre este tema. Sé perfectamente qué debería hacer. El problema es que no lo hago.",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Metáfora Blindaje",
        "categoria":          "distinciones",
        "respuesta_rejected": "A veces el exceso de información puede paralizarnos. Quizás el siguiente paso sea empezar con algo pequeño aunque no sea perfecto.",
        "respuesta_chosen":   "Saber qué hay que hacer y no hacerlo no es un problema de conocimiento. Es un problema de quién eres cuando llega el momento. ¿Quién aparece cuando debería aparecer el que lo hace?",
        "supervisor":         "admin",
        "score_rejected":     3,
        "notas":              "DISTINCIÓN: Sabiduría vs Conocimiento. Rejected: normaliza + advisory. Chosen: distingue el saber intelectual del ser en acción — mueve del dominio del conocimiento al del ser.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 159 — La vigilancia: el esfuerzo que nadie ve ───────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              159,
        "user_input":         "Me esfuerzo mucho pero nadie lo reconoce. A veces pienso para qué seguir.",
        "perfil_detectado":   "victima_estancada",
        "llave_maestra":      "Autoridad Ontológica",
        "categoria":          "distinciones",
        "respuesta_rejected": "El reconocimiento externo es importante pero no puede ser la única fuente de motivación. ¿Qué significa para ti lo que estás logrando, independientemente de lo que digan los demás?",
        "respuesta_chosen":   "—¿Para quién estás haciendo el esfuerzo? Porque si lo haces para que lo vean, el reconocimiento ajeno se vuelve el combustible. Y ese combustible siempre falla.",
        "supervisor":         "admin",
        "score_rejected":     4,
        "notas":              "VIGILANCIA ACTIVA — el esfuerzo que depende del reconocimiento externo. Chosen: devuelve la pregunta sobre la fuente del compromiso — si depende de otros, es frágil.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },

    # ── CASO 160 — Transformación como práctica diaria ───────────────────────
    {
        "session_id":         "manual-bloque16",
        "turno":              160,
        "user_input":         "Pensé que cuando tomara la decisión me iba a sentir diferente. Pero me sigo sintiendo igual.",
        "perfil_detectado":   "reflexivo",
        "llave_maestra":      "Declaración de No-Posibilidad",
        "categoria":          "distinciones",
        "respuesta_rejected": "El cambio interno a veces tarda en reflejarse en cómo nos sentimos. Las decisiones importantes generan transformaciones graduales. Dale tiempo.",
        "respuesta_chosen":   "La decisión fue el inicio, no la llegada. Sentirse diferente es el resultado de actuar diferente de forma sostenida — no de decidir una vez. ¿Qué has hecho diferente desde que decidiste?",
        "supervisor":         "admin",
        "score_rejected":     3,
        "notas":              "DISTINCIÓN: Transformación como práctica, no como evento. Rejected: normaliza + 'dale tiempo' (advisory). Chosen: distingue la decisión del proceso — la transformación se sostiene en la acción diaria, no en el momento de declarar.",
        "validado":           True,
        "categoria_dpo":      "distinciones"
    },
]


def insertar():
    print(f"Insertando {len(PARES)} pares DPO (bloque 16, casos 151-160)...")
    ok = 0
    errores = []

    for par in PARES:
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/pares_dpo",
            headers=HEADERS,
            json=par,
            timeout=15
        )
        caso = par.get("llave_maestra") or par.get("categoria") or "general"
        if r.status_code in (200, 201):
            ok += 1
            print(f"  OK Par {par['turno']} — {par['perfil_detectado']} / {caso}")
        else:
            errores.append(par['turno'])
            print(f"  ERR Par {par['turno']} — {r.status_code}: {r.text[:80]}")

    print(f"\n{'='*50}")
    print(f"OK Insertados: {ok}/{len(PARES)}")
    if errores:
        print(f"ERR Turnos con error: {errores}")
    else:
        print("Sin errores.")
    print(f"\nTotal pares DPO acumulados: 152 (bloques 1-15) + {ok} = {152+ok}/200")


if __name__ == "__main__":
    if not SUPABASE_KEY:
        print("ERR Falta SUPABASE_KEY")
        exit(1)
    insertar()
