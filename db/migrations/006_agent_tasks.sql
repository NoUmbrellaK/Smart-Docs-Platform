-- Stop services and back up first: MySQL DDL is not an atomic migration batch.
CREATE TABLE agent_queue_guard (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  CONSTRAINT ck_agent_guard CHECK (id=1)
) ENGINE=InnoDB;
INSERT INTO agent_queue_guard(id) VALUES (1);
CREATE TABLE agent_tasks (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  owner_user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  question VARCHAR(4000) NOT NULL,
  state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'pending',
  confirmed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_agent_project_id (project_id,id),
  KEY ix_agent_state (state,id),
  CONSTRAINT fk_agent_owner FOREIGN KEY (owner_user_id) REFERENCES users(id),
  CONSTRAINT fk_agent_project FOREIGN KEY (project_id) REFERENCES projects(id),
  CONSTRAINT ck_agent_question CHECK (OCTET_LENGTH(question) BETWEEN 1 AND 4000),
  CONSTRAINT ck_agent_state CHECK (state IN ('pending','running','interrupted','cancelled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
CREATE TABLE agent_task_versions (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  file_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  PRIMARY KEY (task_id,file_id),
  CONSTRAINT fk_agent_scope_task FOREIGN KEY (project_id,task_id) REFERENCES agent_tasks(project_id,id),
  CONSTRAINT fk_agent_scope_file FOREIGN KEY (project_id,file_id) REFERENCES files(project_id,id),
  CONSTRAINT fk_agent_scope_version FOREIGN KEY (file_id,version_id) REFERENCES file_versions(file_id,id)
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (6,'agent_tasks');
