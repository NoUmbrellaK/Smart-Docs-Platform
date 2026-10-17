-- Stop application/workers and back up before this non-atomic DDL migration.
ALTER TABLE processing_jobs
  DROP CHECK ck_processing_job_task_type,
  ADD CONSTRAINT ck_processing_job_task_type CHECK (task_type IN ('parse_and_index','vectorize')),
  ADD COLUMN vector_next_chunk SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  ADD COLUMN vector_window_count INT UNSIGNED NOT NULL DEFAULT 0,
  ADD CONSTRAINT ck_vector_progress CHECK (vector_next_chunk<=2000 AND vector_window_count<=4096);
ALTER TABLE file_versions
  DROP CHECK ck_vector_state,
  ADD CONSTRAINT ck_vector_state CHECK (vector_state IN
    ('not_applicable','pending','processing','completed','failed','interrupted','cancelled'));

CREATE TABLE embedding_batches (
  job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  batch_start SMALLINT UNSIGNED NOT NULL,
  result_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  PRIMARY KEY (job_id,batch_start),
  CONSTRAINT fk_embedding_batch_job FOREIGN KEY (job_id) REFERENCES processing_jobs(id),
  CONSTRAINT ck_embedding_batch_start CHECK (batch_start<2000)
) ENGINE=InnoDB;
CREATE TABLE document_embeddings (
  chunk_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  window_ordinal TINYINT UNSIGNED NOT NULL,
  job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  embedding_revision VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  char_start SMALLINT UNSIGNED NOT NULL,
  char_end SMALLINT UNSIGNED NOT NULL,
  vector JSON NOT NULL,
  PRIMARY KEY (chunk_id,window_ordinal),
  KEY ix_embedding_job (job_id),
  CONSTRAINT fk_embedding_chunk FOREIGN KEY (chunk_id) REFERENCES document_chunks(id),
  CONSTRAINT fk_embedding_job FOREIGN KEY (job_id) REFERENCES processing_jobs(id),
  CONSTRAINT ck_embedding_window CHECK (window_ordinal<64 AND char_start<char_end AND char_end<=1200),
  CONSTRAINT ck_embedding_vector CHECK (JSON_TYPE(vector)='ARRAY' AND JSON_LENGTH(vector)=384)
) ENGINE=InnoDB;

-- Seed already published, current, available and explicitly approved versions.
-- Retain the shared 100-pending bound; excess work is explicitly retryable.
SET @vector_slots = GREATEST(0,100-(SELECT COUNT(*) FROM processing_jobs WHERE state='pending'));
INSERT INTO processing_jobs(id,file_version_id,task_type,state,last_error_code,completed_at)
SELECT REPLACE(UUID(),'-',''), eligible.id, 'vectorize',
  IF(eligible.queue_position<=@vector_slots,'pending','failed'),
  IF(eligible.queue_position<=@vector_slots,NULL,'queue_full'),
  IF(eligible.queue_position<=@vector_slots,NULL,UTC_TIMESTAMP(6))
FROM (
  SELECT v.id,ROW_NUMBER() OVER (ORDER BY v.created_at,v.id) AS queue_position
  FROM file_versions v JOIN files f ON f.current_version_id=v.id AND f.id=v.file_id
  JOIN document_results d ON d.version_id=v.id
  WHERE f.deleted_at IS NULL AND v.availability_state='available' AND v.parse_state='completed'
    AND f.remote_ai_policy='remote_ai_allowed' AND v.remote_ai_approved
) eligible;
UPDATE file_versions v JOIN processing_jobs j ON j.file_version_id=v.id AND j.task_type='vectorize'
SET v.vector_state=j.state;
INSERT INTO schema_migrations(version,name) VALUES (5,'vector_results');
