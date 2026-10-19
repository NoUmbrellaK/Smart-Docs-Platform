-- Stop services and back up first: MySQL DDL is not an atomic migration batch.
-- No historical preview is implicitly confirmed or executed.
ALTER TABLE agent_tasks DROP CHECK ck_agent_state,
  MODIFY state VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
  ADD CONSTRAINT ck_agent_state CHECK (state IN ('pending','running','interrupted','cancelled','completed','failed','waiting_confirmation','waiting_execution'));
ALTER TABLE archive_plans ADD UNIQUE KEY uq_archive_plan_task_id (task_id,id);
CREATE TABLE archive_decisions (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revision INT UNSIGNED NOT NULL,
  plan_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  content_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  actor_user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  decision VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  decided_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  operation_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  result_json JSON NULL,
  PRIMARY KEY (task_id,revision),
  UNIQUE KEY uq_archive_operation (operation_id),
  CONSTRAINT fk_archive_decision_task FOREIGN KEY (task_id) REFERENCES agent_tasks(id),
  CONSTRAINT fk_archive_decision_plan FOREIGN KEY (task_id,plan_id) REFERENCES archive_plans(task_id,id),
  CONSTRAINT fk_archive_decision_actor FOREIGN KEY (actor_user_id) REFERENCES users(id),
  CONSTRAINT ck_archive_decision_revision CHECK (revision>0),
  CONSTRAINT ck_archive_decision CHECK ((decision='confirm' AND operation_id IS NOT NULL) OR
    (decision='reject' AND operation_id IS NULL AND result_json IS NULL)),
  CONSTRAINT ck_archive_execution_result CHECK (result_json IS NULL OR JSON_TYPE(result_json)='OBJECT')
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (10,'archive_execution');
