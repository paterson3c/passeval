-- ============================================================
-- Stats anónimas del CLI (extended) — Passeval TFG (final/clean)
-- ============================================================
-- Aplicar DESPUÉS de 01_schema_stats.sql y DESPUÉS de que
-- el CLI esté listo para escribir stats (Tarea T5).
--
-- No crea tablas nuevas: user_queries usa las mismas tablas
-- que el análisis del dataset, con dataset_type='user_submitted'.
-- Este script solo inicializa el registro en stats.datasets.
-- ============================================================

INSERT INTO stats.datasets (name, dataset_type, description)
VALUES (
    'user_queries',
    'user_submitted',
    'Estadísticas anónimas de contraseñas evaluadas por usuarios (opt-in). '
    'Nunca se almacena la contraseña ni ningún hash de ella.'
)
ON CONFLICT (name) DO NOTHING;

INSERT INTO stats.dataset_totals (dataset_id)
SELECT id FROM stats.datasets WHERE name = 'user_queries'
ON CONFLICT DO NOTHING;
