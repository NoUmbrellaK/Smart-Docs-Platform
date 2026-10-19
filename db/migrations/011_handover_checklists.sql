-- Stop services and back up first: MySQL DDL is not an atomic migration batch.
CREATE TABLE handover_checklists (
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  revision INT UNSIGNED NOT NULL,
  items_json JSON NOT NULL,
  remote_ai_approved BOOLEAN NOT NULL DEFAULT FALSE,
  approved_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  approved_at DATETIME(6) NULL,
  last_request_json JSON NOT NULL,
  CONSTRAINT fk_handover_checklist_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT fk_handover_checklist_approver FOREIGN KEY (approved_by) REFERENCES users(id),
  CONSTRAINT ck_handover_checklist_revision CHECK (revision>0),
  CONSTRAINT ck_handover_checklist_items CHECK (JSON_TYPE(items_json)='ARRAY' AND JSON_LENGTH(items_json)<=50),
  CONSTRAINT ck_handover_checklist_request CHECK (JSON_TYPE(last_request_json)='OBJECT'),
  CONSTRAINT ck_handover_checklist_approval CHECK (
    (remote_ai_approved=TRUE AND approved_by IS NOT NULL AND approved_at IS NOT NULL) OR
    (remote_ai_approved=FALSE AND approved_by IS NULL AND approved_at IS NULL))
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (11,'handover_checklists');
