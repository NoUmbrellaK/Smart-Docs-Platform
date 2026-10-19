-- Stop services and back up first; MySQL DDL is not an atomic migration batch.
-- Existing tasks remain unbound. Handover and archive tasks are explicitly separate intents.
ALTER TABLE agent_tasks ADD COLUMN archive_context JSON NULL,
  ADD CONSTRAINT ck_agent_archive_context CHECK (archive_context IS NULL OR (
    handover_context IS NULL AND JSON_TYPE(archive_context)='OBJECT' AND JSON_LENGTH(archive_context)=2 AND
    JSON_CONTAINS_PATH(archive_context,'all','$.revision','$.targets') AND
    JSON_TYPE(JSON_EXTRACT(archive_context,'$.revision'))='INTEGER' AND
    JSON_EXTRACT(archive_context,'$.revision') BETWEEN 1 AND 4294967295 AND
    JSON_TYPE(JSON_EXTRACT(archive_context,'$.targets'))='ARRAY' AND
    JSON_LENGTH(JSON_EXTRACT(archive_context,'$.targets')) BETWEEN 1 AND 100));
INSERT INTO schema_migrations(version,name) VALUES (15,'agent_archive_context');
