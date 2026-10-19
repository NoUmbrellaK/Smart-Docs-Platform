-- Stop services and back up first; MySQL DDL is not an atomic migration batch.
-- NULL means unrecorded, not an empty set of trusted sources. Never backfill old history.
ALTER TABLE agent_tool_calls
  ADD COLUMN source_json JSON NULL,
  ADD COLUMN model_call TINYINT UNSIGNED NULL,
  ADD CONSTRAINT ck_agent_tool_sources CHECK (source_json IS NULL OR (
    JSON_TYPE(source_json)='OBJECT' AND JSON_LENGTH(source_json)=2 AND
    JSON_CONTAINS_PATH(source_json,'all','$.versions','$.citations') AND
    JSON_TYPE(JSON_EXTRACT(source_json,'$.versions'))='ARRAY' AND
    JSON_TYPE(JSON_EXTRACT(source_json,'$.citations'))='ARRAY' AND
    JSON_LENGTH(JSON_EXTRACT(source_json,'$.versions'))<=10 AND
    JSON_LENGTH(JSON_EXTRACT(source_json,'$.citations'))<=10)),
  ADD CONSTRAINT ck_agent_tool_model_call CHECK (model_call IS NULL OR (
    model_call BETWEEN 1 AND 8 AND source_json IS NOT NULL AND
    status IN ('success','processing','unavailable')));
INSERT INTO schema_migrations(version,name) VALUES (12,'agent_tool_sources');
