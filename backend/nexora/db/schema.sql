-- Esquema SQLite de Nexora. Idempotente: se ejecuta en cada arranque.
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- Índice de archivos procesados por la IA.
-- `num` es la clave interna estable que usa FTS5; `id` (UUID) es la que expone la API.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS archivos_indexados (
    num                   INTEGER PRIMARY KEY AUTOINCREMENT,
    id                    TEXT    NOT NULL UNIQUE,
    nombre                TEXT    NOT NULL,
    extension             TEXT    NOT NULL DEFAULT '',
    mime_type             TEXT,
    tamano_bytes          INTEGER NOT NULL DEFAULT 0,
    hash_sha256           TEXT    NOT NULL,
    ruta_original         TEXT    NOT NULL,            -- dónde apareció el archivo
    ruta_actual           TEXT    NOT NULL UNIQUE,     -- ruta completa actual en el PC
    categoria             TEXT,                        -- carpeta relativa a la raíz organizada, p. ej. "Finanzas/Hogar"
    resumen_ejecutivo     TEXT,                        -- 3-4 líneas generadas por la IA
    palabras_clave        TEXT    NOT NULL DEFAULT '[]', -- arreglo JSON
    confianza             REAL    CHECK (confianza IS NULL OR confianza BETWEEN 0 AND 1),
    estado                TEXT    NOT NULL DEFAULT 'procesando'
                          CHECK (estado IN ('procesando', 'pendiente', 'clasificado', 'corregido', 'error')),
    origen_clasificacion  TEXT    CHECK (origen_clasificacion IN ('ia', 'usuario')),
    modelo_llm            TEXT,
    error                 TEXT,
    creado_en             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actualizado_en        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    clasificado_en        TEXT
);
CREATE INDEX IF NOT EXISTS idx_archivos_estado    ON archivos_indexados (estado);
CREATE INDEX IF NOT EXISTS idx_archivos_hash      ON archivos_indexados (hash_sha256);
CREATE INDEX IF NOT EXISTS idx_archivos_categoria ON archivos_indexados (categoria);

-- Búsqueda por nombre, resumen, palabras clave y ruta (sin distinguir tildes).
CREATE VIRTUAL TABLE IF NOT EXISTS archivos_fts USING fts5 (
    nombre, resumen_ejecutivo, palabras_clave, ruta_actual,
    content = 'archivos_indexados', content_rowid = 'num',
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS archivos_fts_ai AFTER INSERT ON archivos_indexados BEGIN
    INSERT INTO archivos_fts (rowid, nombre, resumen_ejecutivo, palabras_clave, ruta_actual)
    VALUES (new.num, new.nombre, new.resumen_ejecutivo, new.palabras_clave, new.ruta_actual);
END;
CREATE TRIGGER IF NOT EXISTS archivos_fts_ad AFTER DELETE ON archivos_indexados BEGIN
    INSERT INTO archivos_fts (archivos_fts, rowid, nombre, resumen_ejecutivo, palabras_clave, ruta_actual)
    VALUES ('delete', old.num, old.nombre, old.resumen_ejecutivo, old.palabras_clave, old.ruta_actual);
END;
CREATE TRIGGER IF NOT EXISTS archivos_fts_au AFTER UPDATE ON archivos_indexados BEGIN
    INSERT INTO archivos_fts (archivos_fts, rowid, nombre, resumen_ejecutivo, palabras_clave, ruta_actual)
    VALUES ('delete', old.num, old.nombre, old.resumen_ejecutivo, old.palabras_clave, old.ruta_actual);
    INSERT INTO archivos_fts (rowid, nombre, resumen_ejecutivo, palabras_clave, ruta_actual)
    VALUES (new.num, new.nombre, new.resumen_ejecutivo, new.palabras_clave, new.ruta_actual);
END;

-- ---------------------------------------------------------------------------
-- Bandeja de dudas (Human-in-the-Loop): clasificaciones con confianza < umbral.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clasificaciones_pendientes (
    id                TEXT PRIMARY KEY,
    archivo_id        TEXT NOT NULL REFERENCES archivos_indexados (id) ON DELETE CASCADE,
    carpeta_sugerida  TEXT NOT NULL,
    confianza         REAL NOT NULL,
    alternativas      TEXT NOT NULL DEFAULT '[]',   -- arreglo JSON de carpetas
    razonamiento      TEXT,
    estado            TEXT NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta', 'resuelta', 'descartada')),
    creado_en         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resuelto_en       TEXT
);
-- Como mucho una duda abierta por archivo.
CREATE UNIQUE INDEX IF NOT EXISTS ux_pendiente_abierta
    ON clasificaciones_pendientes (archivo_id) WHERE estado = 'abierta';

-- ---------------------------------------------------------------------------
-- Dispositivos autorizados: el escritorio (Tauri) y, en la Fase 2, los móviles
-- emparejados por QR. Solo se guarda el hash del token, nunca el token.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispositivos_vinculados (
    id             TEXT PRIMARY KEY,
    nombre         TEXT NOT NULL,
    tipo           TEXT NOT NULL CHECK (tipo IN ('escritorio', 'movil')),
    plataforma     TEXT,                          -- windows, macos, linux, android, ios
    token_hash     TEXT NOT NULL UNIQUE,          -- SHA-256 del token
    token_prefijo  TEXT NOT NULL,                 -- primeros caracteres, para reconocerlo en la UI
    permisos       TEXT NOT NULL DEFAULT '[]',    -- arreglo JSON: ["*"] o ["files:search", "files:explain", ...]
    clave_publica  TEXT,                          -- Fase 2: clave del móvil para cifrado extremo a extremo
    estado         TEXT NOT NULL DEFAULT 'activo' CHECK (estado IN ('activo', 'revocado')),
    vinculado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    ultimo_acceso  TEXT,
    ultima_ip      TEXT,
    revocado_en    TEXT
);

-- Códigos de un solo uso que el escritorio muestra como QR para emparejar un móvil.
CREATE TABLE IF NOT EXISTS codigos_emparejamiento (
    id              TEXT PRIMARY KEY,
    codigo_hash     TEXT NOT NULL UNIQUE,
    creado_por      TEXT NOT NULL REFERENCES dispositivos_vinculados (id),
    expira_en       TEXT NOT NULL,
    usado_en        TEXT,
    dispositivo_id  TEXT REFERENCES dispositivos_vinculados (id)
);

-- ---------------------------------------------------------------------------
-- Historial de aprendizaje: cada decisión humana sobre una clasificación.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback_aprendizaje (
    id                  TEXT PRIMARY KEY,           -- también es el id del ejemplo en ChromaDB
    archivo_id          TEXT REFERENCES archivos_indexados (id) ON DELETE SET NULL,
    carpeta_sugerida    TEXT,
    carpeta_elegida     TEXT NOT NULL,
    confianza_original  REAL,
    fue_correccion      INTEGER NOT NULL CHECK (fue_correccion IN (0, 1)),
    motivo              TEXT,                       -- lo que escribió el usuario al corregir
    dispositivo_id      TEXT REFERENCES dispositivos_vinculados (id),
    creado_en           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_feedback_archivo ON feedback_aprendizaje (archivo_id);

-- ---------------------------------------------------------------------------
-- Carpetas vigiladas y ajustes generales.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS carpetas_vigiladas (
    id         TEXT PRIMARY KEY,
    ruta       TEXT NOT NULL UNIQUE,
    recursiva  INTEGER NOT NULL DEFAULT 0 CHECK (recursiva IN (0, 1)),
    activa     INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    creado_en  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Archivos que llegan desde el teléfono, por fragmentos y reanudables.
CREATE TABLE IF NOT EXISTS subidas (
    id               TEXT PRIMARY KEY,
    dispositivo_id   TEXT NOT NULL REFERENCES dispositivos_vinculados (id) ON DELETE CASCADE,
    cliente_id       TEXT NOT NULL,              -- id del elemento en la cola del teléfono: reenviar no duplica
    nombre           TEXT NOT NULL,
    tamano           INTEGER NOT NULL,
    hash_sha256      TEXT NOT NULL,
    estado           TEXT NOT NULL DEFAULT 'recibiendo'
                     CHECK (estado IN ('recibiendo', 'recibida', 'organizada', 'error', 'cancelada')),
    bytes_recibidos  INTEGER NOT NULL DEFAULT 0,
    archivo_id       TEXT REFERENCES archivos_indexados (id) ON DELETE SET NULL,
    error            TEXT,
    creado_en        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actualizado_en   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (dispositivo_id, cliente_id)
);

-- Movimientos hechos por Nexora (para poder deshacerlos).
CREATE TABLE IF NOT EXISTS movimientos (
    id                  TEXT PRIMARY KEY,
    archivo_id          TEXT NOT NULL REFERENCES archivos_indexados (id) ON DELETE CASCADE,
    origen              TEXT NOT NULL,
    destino             TEXT NOT NULL,
    categoria_anterior  TEXT,
    autor               TEXT NOT NULL CHECK (autor IN ('ia', 'usuario')),
    creado_en           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deshecho_en         TEXT
);
CREATE INDEX IF NOT EXISTS idx_movimientos_archivo ON movimientos (archivo_id);

-- Indicaciones permanentes del usuario ("los recibos van en Hogar").
CREATE TABLE IF NOT EXISTS indicaciones (
    num        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    texto      TEXT NOT NULL,                    -- lo que escribió el usuario
    resumen    TEXT NOT NULL,                    -- cómo lo entendió la IA, confirmado por el usuario
    accion     TEXT NOT NULL CHECK (accion IN ('mover', 'preguntar', 'no_mover', 'guia')),
    carpeta    TEXT,                             -- solo con 'mover'
    activa     INTEGER NOT NULL DEFAULT 1 CHECK (activa IN (0, 1)),
    usos       INTEGER NOT NULL DEFAULT 0,
    creado_en  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS ajustes (
    clave           TEXT PRIMARY KEY,
    valor           TEXT NOT NULL,
    actualizado_en  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
