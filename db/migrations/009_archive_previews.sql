-- Stop services and back up first: MySQL DDL is not an atomic migration batch.
CREATE TABLE archive_policies (
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  revision INT UNSIGNED NOT NULL,
  targets_json JSON NOT NULL,
  remote_ai_approved BOOLEAN NOT NULL DEFAULT FALSE,
  approved_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  approved_at DATETIME(6) NULL,
  last_request_json JSON NULL,
  CONSTRAINT fk_archive_policy_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_archive_policy_approver FOREIGN KEY (approved_by) REFERENCES users(id),
  CONSTRAINT ck_archive_policy_revision CHECK (revision>0),
  CONSTRAINT ck_archive_policy_targets CHECK (JSON_TYPE(targets_json)='ARRAY' AND JSON_LENGTH(targets_json)<=100),
  CONSTRAINT ck_archive_policy_approval CHECK (
    (remote_ai_approved=TRUE AND approved_by IS NOT NULL AND approved_at IS NOT NULL) OR
    (remote_ai_approved=FALSE AND approved_by IS NULL AND approved_at IS NULL))
) ENGINE=InnoDB;

CREATE TABLE archive_plans (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  revision INT UNSIGNED NOT NULL,
  request_json JSON NOT NULL,
  preview_json JSON NOT NULL,
  UNIQUE KEY uq_archive_plan_id (id),
  CONSTRAINT fk_archive_plan_task FOREIGN KEY (project_id,task_id) REFERENCES agent_tasks(project_id,id),
  CONSTRAINT ck_archive_plan_revision CHECK (revision>0),
  CONSTRAINT ck_archive_plan_request CHECK (JSON_TYPE(request_json)='OBJECT'),
  CONSTRAINT ck_archive_plan_preview CHECK (JSON_TYPE(preview_json)='OBJECT')
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (9,'archive_previews');
