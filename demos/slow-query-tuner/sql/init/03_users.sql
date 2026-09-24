-- =============================================================================
-- Sandboxed identities — the guardrail layer for an autonomous loop.
-- =============================================================================
-- tuner_agent is who the AGENT acts as: read-only, quota'd, blast radius = this
-- throwaway lab only. tuner_admin (the container's default user) keeps DDL and
-- is used by the app ONLY inside the human-approved propose_ddl path.
-- =============================================================================

-- The agent's identity: read-only, quota'd.
CREATE USER IF NOT EXISTS tuner_agent IDENTIFIED WITH sha256_password BY 'tuner_agent123'
    SETTINGS readonly = 2,                -- may SET per-query settings; cannot write
             max_execution_time = 30,
             max_memory_usage = 4000000000,
             max_result_rows = 10000,
             max_result_bytes = 50000000;

GRANT SELECT ON tuning_lab.* TO tuner_agent;
GRANT SHOW TABLES, SHOW COLUMNS ON tuning_lab.* TO tuner_agent;

-- Deliberately NOT granted to tuner_agent: INSERT, ALTER, CREATE, DROP,
-- TRUNCATE, KILL, or access to system.query_log. tuner_admin (the container
-- default user) retains DDL; the app opens an admin connection ONLY inside the
-- human-approved propose_ddl path.
