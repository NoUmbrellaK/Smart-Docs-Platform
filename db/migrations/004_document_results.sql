-- Stop all application/worker instances; back up before applying non-atomic DDL.
ALTER TABLE file_versions
  ADD COLUMN parse_state VARCHAR(16) NOT NULL DEFAULT 'pending',
  ADD COLUMN keyword_state VARCHAR(16) NOT NULL DEFAULT 'pending',
  ADD COLUMN vector_state VARCHAR(16) NOT NULL DEFAULT 'not_applicable',
  ADD CONSTRAINT ck_parse_state CHECK (parse_state IN ('pending','processing','completed','failed','interrupted','cancelled')),
  ADD CONSTRAINT ck_keyword_state CHECK (keyword_state IN ('pending','completed','failed')),
  ADD CONSTRAINT ck_vector_state CHECK (vector_state IN ('not_applicable','pending','completed','failed'));

-- Old schema has no persisted parser results: never manufacture readiness.
UPDATE processing_jobs SET state='interrupted',last_error_code='results_unavailable',
  last_error_message=NULL,completed_at=UTC_TIMESTAMP(6) WHERE state='completed';
UPDATE file_versions SET processing_state='interrupted' WHERE processing_state='completed';
UPDATE file_versions v JOIN files f ON f.id=v.file_id
SET v.parse_state=v.processing_state,
    v.vector_state=IF(f.remote_ai_policy='remote_ai_allowed' AND v.remote_ai_approved,'pending','not_applicable');

CREATE TABLE document_results (
  version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  source_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  parser_revision VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  result_sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  page_count SMALLINT UNSIGNED NOT NULL,
  pages_without_text JSON NOT NULL,
  chunk_count SMALLINT UNSIGNED NOT NULL,
  char_count INT UNSIGNED NOT NULL,
  published_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_document_version FOREIGN KEY (version_id) REFERENCES file_versions(id),
  CONSTRAINT ck_document_limits CHECK (page_count<=300 AND chunk_count BETWEEN 1 AND 2000 AND char_count BETWEEN 1 AND 2000000)
) ENGINE=InnoDB;

CREATE TABLE document_chunks (
  id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
  version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  ordinal SMALLINT UNSIGNED NOT NULL,
  page SMALLINT UNSIGNED NULL,
  char_start INT UNSIGNED NOT NULL,
  char_end INT UNSIGNED NOT NULL,
  line_start INT UNSIGNED NOT NULL,
  line_end INT UNSIGNED NOT NULL,
  body TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL,
  UNIQUE KEY uq_document_ordinal (version_id,ordinal),
  FULLTEXT KEY ft_document_body (body) WITH PARSER ngram,
  CONSTRAINT fk_chunk_document FOREIGN KEY (version_id) REFERENCES document_results(version_id),
  CONSTRAINT ck_chunk_location CHECK (ordinal<2000 AND (page IS NULL OR page BETWEEN 1 AND 300)
    AND char_end>char_start AND char_end-char_start<=1200 AND char_end<=2000000
    AND line_start>0 AND line_end>=line_start)
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (4,'document_results');
