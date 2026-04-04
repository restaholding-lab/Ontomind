-- ═══════════════════════════════════════════════════════════
-- ONTOMIND — Esquema Supabase
-- Ejecutar en: Supabase Dashboard → SQL Editor
-- ═══════════════════════════════════════════════════════════

-- Tabla principal: Mapa del Observador
CREATE TABLE IF NOT EXISTS mapa_observador (
    id                    BIGSERIAL PRIMARY KEY,
    session_id            TEXT UNIQUE NOT NULL,
    ultima_posicion       TEXT,           -- victima | protagonista | mixto
    ultimo_quiebre        TEXT,           -- tecnico | ontologico
    ancora_activado       BOOLEAN DEFAULT FALSE,
    turnos_desde_ancora   INTEGER DEFAULT 999,
    historial_posiciones  JSONB DEFAULT '[]',
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Tabla de alertas VIGIL (solo supervisores)
CREATE TABLE IF NOT EXISTS alertas_vigil (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT NOT NULL,
    nivel       TEXT NOT NULL,   -- latente | alto | critico
    mensaje     TEXT,
    timestamp   TIMESTAMPTZ DEFAULT NOW(),
    revisado    BOOLEAN DEFAULT FALSE
);

-- Índices
CREATE INDEX IF NOT EXISTS idx_mapa_session ON mapa_observador(session_id);
CREATE INDEX IF NOT EXISTS idx_alertas_session ON alertas_vigil(session_id);
CREATE INDEX IF NOT EXISTS idx_alertas_revisado ON alertas_vigil(revisado);

-- Upsert automático al actualizar el mapa
CREATE OR REPLACE FUNCTION upsert_mapa_observador(
    p_session_id          TEXT,
    p_ultima_posicion     TEXT,
    p_ultimo_quiebre      TEXT,
    p_ancora_activado     BOOLEAN,
    p_turnos_ancora       INTEGER,
    p_historial           JSONB
) RETURNS VOID AS $$
BEGIN
    INSERT INTO mapa_observador (
        session_id, ultima_posicion, ultimo_quiebre,
        ancora_activado, turnos_desde_ancora,
        historial_posiciones, updated_at
    )
    VALUES (
        p_session_id, p_ultima_posicion, p_ultimo_quiebre,
        p_ancora_activado, p_turnos_ancora,
        p_historial, NOW()
    )
    ON CONFLICT (session_id) DO UPDATE SET
        ultima_posicion      = EXCLUDED.ultima_posicion,
        ultimo_quiebre       = EXCLUDED.ultimo_quiebre,
        ancora_activado      = EXCLUDED.ancora_activado,
        turnos_desde_ancora  = EXCLUDED.turnos_desde_ancora,
        historial_posiciones = EXCLUDED.historial_posiciones,
        updated_at           = NOW();
END;
$$ LANGUAGE plpgsql;
