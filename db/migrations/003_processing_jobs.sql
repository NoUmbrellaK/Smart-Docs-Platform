-- Durable local processing lifecycle; stop application instances before migration.
CREATE TABLE processing_queue_guard (
  id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
  CONSTRAINT ck_processing_queue_singleton CHECK (id = 1)
) ENGINE=InnoDB;
INSERT INTO processing_queue_guard(id) VALUES (1);

ALTER TABLE processing_jobs
  DROP CHECK ck_processing_job_state,
  ADD CONSTRAINT ck_processing_job_state CHECK (
    state IN ('pending','running','completed','failed','interrupted','cancelled')),
  ADD COLUMN attempt_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL;
ALTER TABLE file_versions
  DROP CHECK ck_file_version_processing_state,
  ADD CONSTRAINT ck_file_version_processing_state CHECK (
    processing_state IN ('pending','processing','completed','failed','interrupted','cancelled'));

UPDATE processing_jobs SET state='interrupted', last_error_code='service_restarted',
  last_error_message=NULL, completed_at=UTC_TIMESTAMP(6) WHERE state='running';
UPDATE processing_jobs j JOIN (
  SELECT id, ROW_NUMBER() OVER (ORDER BY created_at,id) AS position_in_queue
  FROM processing_jobs WHERE state='pending'
) ranked ON ranked.id=j.id
SET j.state='failed', j.last_error_code='queue_full', j.last_error_message=NULL,
    j.completed_at=UTC_TIMESTAMP(6)
WHERE ranked.position_in_queue > 100;
UPDATE file_versions v JOIN processing_jobs j ON j.file_version_id=v.id
SET v.processing_state=j.state
WHERE j.state='interrupted' OR (j.state='failed' AND j.last_error_code='queue_full');
ALTER TABLE processing_jobs
  ADD CONSTRAINT ck_processing_attempt_token CHECK (
    (attempt_token IS NULL OR attempt_token REGEXP '^[0-9a-f]{32}$')
    AND (state <> 'running' OR attempt_token IS NOT NULL));

INSERT INTO schema_migrations(version,name) VALUES (3,'processing_jobs');
