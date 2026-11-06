-- Stop services and back up first; MySQL DDL plus version bookkeeping is not one transaction.
-- Existing tasks remain unrestricted. NULL never invents historical inspection consent.
ALTER TABLE agent_tasks ADD COLUMN inspection_constraints JSON NULL,
  ADD CONSTRAINT ck_agent_inspection_constraints CHECK (inspection_constraints IS NULL OR (
    JSON_TYPE(inspection_constraints)='OBJECT' AND JSON_LENGTH(inspection_constraints)=3 AND
    JSON_CONTAINS_PATH(inspection_constraints,'all','$.mode','$.max_page','$.page_size') AND
    JSON_TYPE(JSON_EXTRACT(inspection_constraints,'$.mode'))='STRING' AND
    JSON_UNQUOTE(JSON_EXTRACT(inspection_constraints,'$.mode'))='metadata' AND
    JSON_TYPE(JSON_EXTRACT(inspection_constraints,'$.max_page'))='INTEGER' AND
    JSON_EXTRACT(inspection_constraints,'$.max_page') BETWEEN 1 AND 1000 AND
    JSON_TYPE(JSON_EXTRACT(inspection_constraints,'$.page_size'))='INTEGER' AND
    JSON_EXTRACT(inspection_constraints,'$.page_size') BETWEEN 1 AND 10));
INSERT INTO schema_migrations(version,name) VALUES (17,'task_inspection_constraints');
