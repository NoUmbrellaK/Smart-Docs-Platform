-- Stop services and back up before this non-atomic MySQL DDL batch.
ALTER TABLE agent_tasks DROP CHECK ck_agent_state,
  ADD CONSTRAINT ck_agent_state CHECK (state IN ('pending','running','interrupted','cancelled','completed','failed'));
CREATE TABLE agent_runs (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  model_calls TINYINT UNSIGNED NOT NULL DEFAULT 0,
  result_json JSON NULL,
  finish_code VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  CONSTRAINT fk_agent_run_task FOREIGN KEY (task_id) REFERENCES agent_tasks(id),
  CONSTRAINT ck_agent_model_calls CHECK (model_calls<=8)
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (7,'agent_runs');
