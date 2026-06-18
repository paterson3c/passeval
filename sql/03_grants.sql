-- ============================================================
-- Grants adicionales — Passeval TFG
-- ============================================================
-- Los grants del schema stats ya están incluidos en
-- 01_schema_stats.sql. Este fichero añade acceso al schema
-- public por si alguna herramienta o extensión lo necesita.
-- ============================================================

GRANT USAGE ON SCHEMA public TO passeval_user;
GRANT ALL ON ALL TABLES IN SCHEMA public TO passeval_user;
