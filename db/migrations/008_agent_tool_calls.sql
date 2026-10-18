-- Stop services and back up before this non-atomic MySQL DDL batch.
CREATE TABLE agent_tool_calls (
  task_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  sequence TINYINT UNSIGNED NOT NULL,
  request_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  tool VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  parameters JSON NOT NULL,
  status VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'unknown',
  started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  duration_ms BIGINT UNSIGNED NULL,
  PRIMARY KEY (task_id,sequence),
  UNIQUE KEY uq_agent_tool_request (request_id),
  CONSTRAINT fk_agent_tool_task FOREIGN KEY (task_id) REFERENCES agent_tasks(id),
  CONSTRAINT ck_agent_tool_sequence CHECK (sequence BETWEEN 1 AND 64),
  CONSTRAINT ck_agent_tool_name CHECK (tool IN ('search_documents','read_document_version')),
  CONSTRAINT ck_agent_tool_status CHECK (status IN ('unknown','success','processing','unavailable',
    'invalid_request','access_denied','conflict','busy','timeout','dependency_failed')),
  CONSTRAINT ck_agent_tool_duration CHECK ((status='unknown' AND duration_ms IS NULL) OR
    (status<>'unknown' AND duration_ms IS NOT NULL))
) ENGINE=InnoDB;
INSERT INTO schema_migrations(version,name) VALUES (8,'agent_tool_calls');
