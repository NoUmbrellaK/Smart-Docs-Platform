-- Stop services and back up first; MySQL DDL plus version bookkeeping is not one transaction.
-- Preserve all prior rows; do not invent historical write calls.
ALTER TABLE agent_tool_calls
  DROP CHECK ck_agent_tool_name,
  DROP CHECK ck_agent_tool_status,
  ADD CONSTRAINT ck_agent_tool_name CHECK (tool IN ('search_documents','read_document_version',
    'create_archive_plan','execute_archive_plan','save_handover_report')),
  ADD CONSTRAINT ck_agent_tool_status CHECK (status IN ('unknown','success','processing','unavailable',
    'waiting_confirmation','invalid_request','access_denied','conflict','busy','timeout','dependency_failed'));
INSERT INTO schema_migrations(version,name) VALUES (16,'write_tool_audit');
