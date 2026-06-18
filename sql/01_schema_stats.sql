-- ============================================================
-- SCHEMA stats — Passeval TFG (final/clean)
-- ============================================================
-- Aplicar con:
--   psql -U passeval_user -d passeval_db -f sql/01_schema_stats.sql
-- ============================================================

DROP SCHEMA IF EXISTS stats CASCADE;
CREATE SCHEMA stats AUTHORIZATION passeval_user;

-- ------------------------------------------------------------
-- Catálogo de datasets
-- dataset_type: 'leaked' (filtraciones) | 'user_submitted' (CLI opt-in)
-- ------------------------------------------------------------
CREATE TABLE stats.datasets (
    id           BIGSERIAL PRIMARY KEY,
    name         VARCHAR(100) UNIQUE NOT NULL,
    dataset_type VARCHAR(20)  NOT NULL DEFAULT 'leaked'
                 CHECK (dataset_type IN ('leaked', 'user_submitted')),
    source       VARCHAR(255),
    description  TEXT,
    ingested_at  TIMESTAMPTZ DEFAULT now(),
    notes        JSONB
) TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Sumas acumuladas por dataset
-- Medias exactas: mean_X = sum_X / total_lines
-- Se actualiza en la misma transacción que los histogramas (por batch).
-- sum_guesses solo se puebla con --mode full.
-- ------------------------------------------------------------
CREATE TABLE stats.dataset_totals (
    dataset_id       BIGINT        PRIMARY KEY
                     REFERENCES stats.datasets(id) ON DELETE CASCADE,
    total_lines      BIGINT        NOT NULL DEFAULT 0,
    sum_length       BIGINT        NOT NULL DEFAULT 0,
    sum_shannon      NUMERIC(24,6) NOT NULL DEFAULT 0,
    sum_charset_bits NUMERIC(24,6) NOT NULL DEFAULT 0,
    sum_guesses      NUMERIC(24,6),
    updated_at       TIMESTAMPTZ DEFAULT now()
) TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Histograma de longitudes
-- ------------------------------------------------------------
CREATE TABLE stats.length_histogram (
    dataset_id BIGINT   NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    length     SMALLINT NOT NULL,
    count      BIGINT   NOT NULL,
    PRIMARY KEY (dataset_id, length)
) TABLESPACE passeval_ts;

CREATE INDEX idx_length_hist_count
    ON stats.length_histogram (dataset_id, count DESC)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Histograma de composición (charset)
-- Bitmask: 1=lower 2=upper 4=digit 8=symbol 16=unicode
-- ------------------------------------------------------------
CREATE TABLE stats.charset_histogram (
    dataset_id   BIGINT   NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    charset_mask SMALLINT NOT NULL,
    count        BIGINT   NOT NULL,
    PRIMARY KEY (dataset_id, charset_mask)
) TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Histograma de entropía
-- entropy_type: 'shannon' | 'charset'
-- bucket_min: límite inferior del bucket (paso 0.5 bits)
-- ------------------------------------------------------------
CREATE TABLE stats.entropy_histogram (
    dataset_id   BIGINT       NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    entropy_type VARCHAR(10)  NOT NULL,
    bucket_min   NUMERIC(10,2) NOT NULL,
    count        BIGINT       NOT NULL,
    PRIMARY KEY (dataset_id, entropy_type, bucket_min)
) TABLESPACE passeval_ts;

CREATE INDEX idx_entropy_hist_type
    ON stats.entropy_histogram (dataset_id, entropy_type)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Estadísticas de patrones detectados
-- pattern_type: 'dictionary'|'date'|'keyboard'|'leet'|
--               'repetition'|'sequence'|'spanish'
-- ------------------------------------------------------------
CREATE TABLE stats.pattern_stats (
    dataset_id   BIGINT       NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    pattern_type VARCHAR(20)  NOT NULL,
    pattern_repr VARCHAR(255) NOT NULL,
    count        BIGINT       NOT NULL,
    PRIMARY KEY (dataset_id, pattern_type, pattern_repr)
) TABLESPACE passeval_ts;

CREATE INDEX idx_pattern_stats_count
    ON stats.pattern_stats (dataset_id, count DESC)
    TABLESPACE passeval_ts;

CREATE INDEX idx_pattern_stats_type_count
    ON stats.pattern_stats (dataset_id, pattern_type, count DESC)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Frecuencias de tokens
-- token_type: 'substring' (palabras de diccionario halladas dentro
--             de la contraseña) | 'deleetified' (forma deleet)
-- ------------------------------------------------------------
CREATE TABLE stats.token_frequencies (
    dataset_id BIGINT      NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    token_type VARCHAR(15) NOT NULL,
    token      VARCHAR(64) NOT NULL,
    count      BIGINT      NOT NULL,
    PRIMARY KEY (dataset_id, token_type, token)
) TABLESPACE passeval_ts;

CREATE INDEX idx_token_freq_count
    ON stats.token_frequencies (dataset_id, token_type, count DESC)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Histograma de score de fortaleza (0-4)
-- Solo se puebla con --mode full.
-- 0=Trivial 1=Muy débil 2=Débil 3=Fuerte 4=Muy fuerte
-- ------------------------------------------------------------
CREATE TABLE stats.score_histogram (
    dataset_id BIGINT   NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    score      SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 4),
    count      BIGINT   NOT NULL,
    PRIMARY KEY (dataset_id, score)
) TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Log de ejecuciones del ETL
-- ------------------------------------------------------------
CREATE TABLE stats.ingestion_runs (
    id              BIGSERIAL PRIMARY KEY,
    dataset_id      BIGINT      REFERENCES stats.datasets(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(15) NOT NULL
                    CHECK (status IN ('running','completed','failed','interrupted')),
    files_processed INTEGER     DEFAULT 0,
    lines_read      BIGINT      DEFAULT 0,
    lines_valid     BIGINT      DEFAULT 0,
    lines_invalid   BIGINT      DEFAULT 0,
    error_message   TEXT
) TABLESPACE passeval_ts;

CREATE INDEX idx_ingestion_runs_dataset
    ON stats.ingestion_runs (dataset_id)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Progreso por fichero (reanudabilidad)
-- Se actualiza cada batch (~1M líneas) dentro de la misma
-- transacción que los histogramas — nunca diverge de los datos.
-- ------------------------------------------------------------
CREATE TABLE stats.file_progress (
    dataset_id      BIGINT      NOT NULL REFERENCES stats.datasets(id) ON DELETE CASCADE,
    filename        TEXT        NOT NULL,
    status          VARCHAR(15) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed')),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    lines_processed BIGINT      DEFAULT 0,
    lines_valid     BIGINT      DEFAULT 0,
    lines_invalid   BIGINT      DEFAULT 0,
    error_message   TEXT,
    PRIMARY KEY (dataset_id, filename)
) TABLESPACE passeval_ts;

CREATE INDEX idx_file_progress_status
    ON stats.file_progress (dataset_id, status)
    TABLESPACE passeval_ts;


-- ------------------------------------------------------------
-- Grants
-- ------------------------------------------------------------
GRANT USAGE ON SCHEMA stats TO passeval_user;
GRANT ALL ON ALL TABLES IN SCHEMA stats TO passeval_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA stats TO passeval_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats
    GRANT ALL ON TABLES TO passeval_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA stats
    GRANT ALL ON SEQUENCES TO passeval_user;
