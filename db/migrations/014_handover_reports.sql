-- Stop services and back up first; DDL is not one atomic migration batch.
-- Existing runs remain NULL and cannot invent a report-save operation.
ALTER TABLE agent_runs
  ADD COLUMN report_operation_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  ADD UNIQUE KEY uq_report_run_operation (report_operation_id),
  ADD UNIQUE KEY uq_report_run_binding (task_id,report_operation_id);
CREATE TABLE handover_reports (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  operation_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  version TINYINT UNSIGNED NOT NULL DEFAULT 1,
  request_json JSON NOT NULL,
  content_json JSON NOT NULL,
  sources_json JSON NOT NULL,
  checklist_json JSON NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_report_task (task_id),
  UNIQUE KEY uq_report_operation (operation_id),
  CONSTRAINT fk_report_run FOREIGN KEY (task_id,operation_id) REFERENCES agent_runs(task_id,report_operation_id),
  CONSTRAINT ck_report_version CHECK (version=1),
  CONSTRAINT ck_report_objects CHECK (JSON_TYPE(request_json)='OBJECT' AND JSON_TYPE(content_json)='OBJECT'
    AND JSON_TYPE(checklist_json)='OBJECT' AND JSON_TYPE(sources_json)='OBJECT'
    AND JSON_CONTAINS_PATH(sources_json,'all','$.versions','$.citations')
    AND JSON_TYPE(JSON_EXTRACT(sources_json,'$.versions'))='ARRAY'
    AND JSON_TYPE(JSON_EXTRACT(sources_json,'$.citations'))='ARRAY'
    AND JSON_LENGTH(JSON_EXTRACT(sources_json,'$.versions'))<=100
    AND JSON_LENGTH(JSON_EXTRACT(sources_json,'$.citations'))<=640)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
INSERT INTO schema_migrations(version,name) VALUES (14,'handover_reports');
