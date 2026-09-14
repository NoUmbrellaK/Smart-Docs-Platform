CREATE TABLE schema_migrations (
  version INT UNSIGNED NOT NULL,
  name VARCHAR(255) NOT NULL,
  applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE users (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  login_name VARCHAR(64) NOT NULL,
  password_salt CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  password_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  password_iterations INT UNSIGNED NOT NULL,
  status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_user_login_name (login_name),
  CONSTRAINT ck_user_password_salt CHECK (
    password_salt REGEXP '^[0-9a-f]{32}$'),
  CONSTRAINT ck_user_password_hash CHECK (
    password_hash REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT ck_user_password_iterations CHECK (password_iterations > 0),
  CONSTRAINT ck_user_status CHECK (status IN ('active', 'disabled'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE auth_sessions (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  revoked_at DATETIME(6) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uq_auth_session_token_hash (token_hash),
  KEY ix_auth_session_user (user_id),
  CONSTRAINT ck_auth_session_token_hash CHECK (
    token_hash REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT fk_auth_session_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE projects (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  name VARCHAR(255) NOT NULL,
  created_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  root_directory_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY ix_project_creator (created_by),
  CONSTRAINT fk_project_creator FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE project_members (
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  role VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (project_id, user_id),
  KEY ix_project_member_user (user_id, project_id),
  CONSTRAINT ck_project_member_role CHECK (
    role IN ('admin', 'editor', 'reader')),
  CONSTRAINT fk_project_member_project FOREIGN KEY (project_id)
    REFERENCES projects(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_project_member_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE directories (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  parent_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  name VARCHAR(255) NOT NULL,
  created_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY ix_directory_creator (created_by),
  CONSTRAINT fk_directory_project FOREIGN KEY (project_id)
    REFERENCES projects(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_directory_creator FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE directories
  ADD COLUMN parent_scope CHAR(32) CHARACTER SET ascii COLLATE ascii_bin
    GENERATED ALWAYS AS (
      COALESCE(parent_id, '00000000000000000000000000000000')) STORED,
  ADD COLUMN root_marker TINYINT
    GENERATED ALWAYS AS (
      CASE WHEN parent_id IS NULL THEN 1 ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_directory_sibling (project_id, parent_scope, name),
  ADD UNIQUE KEY uq_project_root (project_id, root_marker),
  ADD UNIQUE KEY uq_directory_project_id (project_id, id),
  ADD CONSTRAINT fk_directory_parent FOREIGN KEY (project_id, parent_id)
    REFERENCES directories(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

ALTER TABLE projects
  ADD CONSTRAINT fk_project_root_directory FOREIGN KEY (id, root_directory_id)
    REFERENCES directories(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE files (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  directory_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  name VARCHAR(255) NOT NULL,
  current_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  remote_ai_policy VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    DEFAULT 'internal_only',
  deleted_at DATETIME(6) NULL,
  deleted_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY ix_file_creator (created_by),
  KEY ix_file_deleted_by (deleted_by),
  CONSTRAINT ck_file_remote_ai_policy CHECK (
    remote_ai_policy IN ('internal_only', 'remote_ai_allowed')),
  CONSTRAINT fk_file_project FOREIGN KEY (project_id) REFERENCES projects(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_file_directory FOREIGN KEY (project_id, directory_id)
    REFERENCES directories(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_file_creator FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_file_deleted_by FOREIGN KEY (deleted_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE files
  ADD COLUMN active_name VARCHAR(255)
    GENERATED ALWAYS AS (
      CASE WHEN deleted_at IS NULL THEN name ELSE NULL END) STORED,
  ADD UNIQUE KEY uq_active_file_name (project_id, directory_id, active_name),
  ADD UNIQUE KEY uq_file_project_id (project_id, id);

CREATE TABLE file_versions (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  file_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  version_number INT UNSIGNED NOT NULL,
  content_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  size_bytes BIGINT UNSIGNED NOT NULL,
  sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  media_type VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  created_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  processing_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    DEFAULT 'pending',
  availability_state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    DEFAULT 'available',
  remote_ai_approved BOOLEAN NOT NULL DEFAULT FALSE,
  remote_ai_approved_at DATETIME(6) NULL,
  remote_ai_approved_by CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY ix_file_version_creator (created_by),
  KEY ix_file_version_approver (remote_ai_approved_by),
  CONSTRAINT ck_file_version_number CHECK (version_number > 0),
  CONSTRAINT ck_file_version_size CHECK (size_bytes >= 0),
  CONSTRAINT ck_file_version_sha256 CHECK (
    sha256 REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT ck_file_version_processing_state CHECK (
    processing_state IN ('pending', 'processing', 'completed', 'failed')),
  CONSTRAINT ck_file_version_availability CHECK (
    availability_state IN ('available', 'unavailable')),
  CONSTRAINT ck_file_version_remote_approval CHECK (
    (remote_ai_approved = FALSE AND remote_ai_approved_at IS NULL
      AND remote_ai_approved_by IS NULL)
    OR
    (remote_ai_approved = TRUE AND remote_ai_approved_at IS NOT NULL
      AND remote_ai_approved_by IS NOT NULL)),
  CONSTRAINT fk_file_version_file FOREIGN KEY (file_id) REFERENCES files(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_file_version_creator FOREIGN KEY (created_by) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_file_version_approver FOREIGN KEY (remote_ai_approved_by)
    REFERENCES users(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE file_versions
  ADD UNIQUE KEY uq_file_version_number (file_id, version_number),
  ADD UNIQUE KEY uq_content_id (content_id),
  ADD UNIQUE KEY uq_version_file_id (file_id, id);

ALTER TABLE files
  ADD CONSTRAINT fk_file_current_version FOREIGN KEY (id, current_version_id)
    REFERENCES file_versions(file_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE upload_tasks (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  owner_user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  target_directory_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  target_file_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  observed_current_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  mode VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  expected_name VARCHAR(255) NULL,
  expected_size_bytes BIGINT UNSIGNED NOT NULL,
  expected_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  media_type VARCHAR(255) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    DEFAULT 'application/octet-stream',
  chunk_size_bytes BIGINT UNSIGNED NOT NULL,
  total_parts INT UNSIGNED NOT NULL,
  state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  failure_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  failure_message VARCHAR(255) NULL,
  result_file_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  result_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  processing_job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  completed_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  KEY ix_upload_owner_state (owner_user_id, state, created_at),
  KEY ix_upload_project_state (project_id, state, created_at),
  KEY ix_upload_target_file (target_file_id),
  KEY ix_upload_observed_version (observed_current_version_id),
  KEY ix_upload_result_file (result_file_id),
  KEY ix_upload_result_version (result_version_id),
  CONSTRAINT ck_upload_mode CHECK (mode IN ('create_file', 'create_version')),
  CONSTRAINT ck_upload_expected_size CHECK (expected_size_bytes >= 0),
  CONSTRAINT ck_upload_expected_sha256 CHECK (
    expected_sha256 REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT ck_upload_chunk_size CHECK (chunk_size_bytes > 0),
  CONSTRAINT ck_upload_total_parts CHECK (total_parts > 0),
  CONSTRAINT ck_upload_state CHECK (
    state IN ('uploading', 'assembling', 'publishing', 'completed', 'failed',
              'cancelled', 'interrupted')),
  CONSTRAINT ck_upload_mode_targets CHECK (
    (mode = 'create_file' AND target_directory_id IS NOT NULL
      AND target_file_id IS NULL AND observed_current_version_id IS NULL
      AND expected_name IS NOT NULL)
    OR
    (mode = 'create_version' AND target_directory_id IS NULL
      AND target_file_id IS NOT NULL AND observed_current_version_id IS NOT NULL
      AND expected_name IS NULL)),
  CONSTRAINT ck_upload_completion CHECK (
    (state = 'completed' AND result_file_id IS NOT NULL
      AND result_version_id IS NOT NULL AND processing_job_id IS NOT NULL
      AND completed_at IS NOT NULL)
    OR
    (state <> 'completed' AND result_file_id IS NULL
      AND result_version_id IS NULL AND processing_job_id IS NULL
      AND completed_at IS NULL)),
  CONSTRAINT fk_upload_owner FOREIGN KEY (owner_user_id) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_project FOREIGN KEY (project_id) REFERENCES projects(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_directory FOREIGN KEY (project_id, target_directory_id)
    REFERENCES directories(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_target_file FOREIGN KEY (project_id, target_file_id)
    REFERENCES files(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_observed_version FOREIGN KEY (
    target_file_id, observed_current_version_id)
    REFERENCES file_versions(file_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_result_file FOREIGN KEY (project_id, result_file_id)
    REFERENCES files(project_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_upload_result_version FOREIGN KEY (
    result_file_id, result_version_id)
    REFERENCES file_versions(file_id, id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE upload_parts (
  upload_task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  part_number INT UNSIGNED NOT NULL,
  size_bytes BIGINT UNSIGNED NOT NULL,
  sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  staging_name VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  received_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  CONSTRAINT ck_upload_part_number CHECK (
    part_number > 0 AND part_number <= 1000000),
  CONSTRAINT ck_upload_part_size CHECK (size_bytes > 0),
  CONSTRAINT ck_upload_part_sha256 CHECK (
    sha256 REGEXP '^[0-9a-f]{64}$'),
  CONSTRAINT fk_upload_part_task FOREIGN KEY (upload_task_id)
    REFERENCES upload_tasks(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE upload_parts
  ADD PRIMARY KEY (upload_task_id, part_number);

CREATE TABLE processing_jobs (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  file_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  task_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  state VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
    DEFAULT 'pending',
  attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
  last_error_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  last_error_message VARCHAR(255) NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  completed_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_processing_job_version_type (file_version_id, task_type),
  KEY ix_processing_job_state (state, created_at),
  CONSTRAINT ck_processing_job_task_type CHECK (
    task_type IN ('parse_and_index')),
  CONSTRAINT ck_processing_job_state CHECK (
    state IN ('pending', 'running', 'completed', 'failed')),
  CONSTRAINT ck_processing_job_attempts CHECK (attempt_count >= 0),
  CONSTRAINT fk_processing_job_version FOREIGN KEY (file_version_id)
    REFERENCES file_versions(id) ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE upload_tasks
  ADD CONSTRAINT fk_upload_processing_job FOREIGN KEY (processing_job_id)
    REFERENCES processing_jobs(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT;

CREATE TABLE audit_records (
  id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  actor_user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  project_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  action VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  object_type VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  object_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
  request_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  result VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  detail_code VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  KEY ix_audit_project_created (project_id, created_at),
  KEY ix_audit_actor_created (actor_user_id, created_at),
  KEY ix_audit_request (request_id),
  CONSTRAINT ck_audit_result CHECK (result IN ('success', 'denied', 'error')),
  CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_audit_project FOREIGN KEY (project_id) REFERENCES projects(id)
    ON DELETE RESTRICT ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO schema_migrations(version, name) VALUES (1, 'm1_core');
