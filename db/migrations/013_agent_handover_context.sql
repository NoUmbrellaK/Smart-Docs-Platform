-- Stop services and back up first; MySQL DDL is not an atomic migration batch.
-- NULL is an ordinary task, never infer consent for old tasks.
ALTER TABLE agent_tasks ADD COLUMN handover_context JSON NULL,
  ADD CONSTRAINT ck_agent_handover_context CHECK (handover_context IS NULL OR (
    JSON_TYPE(handover_context)='OBJECT' AND JSON_LENGTH(handover_context)=2 AND
    JSON_CONTAINS_PATH(handover_context,'all','$.revision','$.items') AND
    JSON_TYPE(JSON_EXTRACT(handover_context,'$.revision'))='INTEGER' AND
    JSON_EXTRACT(handover_context,'$.revision') BETWEEN 1 AND 4294967295 AND
    JSON_TYPE(JSON_EXTRACT(handover_context,'$.items'))='ARRAY' AND
    JSON_LENGTH(JSON_EXTRACT(handover_context,'$.items')) BETWEEN 1 AND 50));
INSERT INTO schema_migrations(version,name) VALUES (13,'agent_handover_context');
