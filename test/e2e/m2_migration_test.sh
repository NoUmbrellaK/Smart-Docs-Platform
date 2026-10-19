#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
case ${SMARTDOCS_MYSQL_SOCKET:-} in
    /tmp/smartdocs-mysql.*/mysql.sock) ;;
    *) printf '%s\n' 'migration_fixture_refused: isolated test socket required' >&2; exit 2 ;;
esac
mysql_test=(mysql --protocol=SOCKET --socket="$SMARTDOCS_MYSQL_SOCKET" --user=root --batch --skip-column-names)
"${mysql_test[@]}" --execute="CREATE DATABASE smart_docs_upgrade CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci; GRANT ALL PRIVILEGES ON smart_docs_upgrade.* TO 'smart_docs_test'@'localhost';"
"${mysql_test[@]}" smart_docs_upgrade < "$repo_root/db/migrations/001_m1_core.sql"
"${mysql_test[@]}" smart_docs_upgrade < "$repo_root/db/migrations/002_zero_based_upload_parts.sql"
"${mysql_test[@]}" smart_docs_upgrade <<'SQL'
CREATE TEMPORARY TABLE fixture_numbers(n INT PRIMARY KEY);
INSERT INTO fixture_numbers WITH RECURSIVE seq(n) AS (
  SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<103
) SELECT n FROM seq;
INSERT INTO users(id,login_name,password_salt,password_hash,password_iterations,status)
VALUES (LPAD('1',32,'0'),'migration_fixture',REPEAT('a',32),REPEAT('b',64),1000,'active');
INSERT INTO projects(id,name,created_by) VALUES (LPAD('2',32,'0'),'Migration fixture',LPAD('1',32,'0'));
INSERT INTO directories(id,project_id,parent_id,name,created_by)
VALUES (LPAD('3',32,'0'),LPAD('2',32,'0'),NULL,'Root',LPAD('1',32,'0'));
UPDATE projects SET root_directory_id=LPAD('3',32,'0');
INSERT INTO files(id,project_id,directory_id,name,created_by)
SELECT LPAD(n,32,'0'),LPAD('2',32,'0'),LPAD('3',32,'0'),CONCAT('file-',n),LPAD('1',32,'0') FROM fixture_numbers;
INSERT INTO file_versions(id,file_id,version_number,content_id,size_bytes,sha256,media_type,created_by,processing_state)
SELECT LPAD(n+1000,32,'0'),LPAD(n,32,'0'),1,LPAD(n+2000,32,'0'),3,REPEAT('a',64),'text/plain',LPAD('1',32,'0'),
       IF(n=103,'completed',IF(n=102,'processing','pending')) FROM fixture_numbers;
UPDATE files f JOIN file_versions v ON v.file_id=f.id SET f.current_version_id=v.id;
INSERT INTO processing_jobs(id,file_version_id,task_type,state,attempt_count)
SELECT LPAD(n+3000,32,'0'),LPAD(n+1000,32,'0'),'parse_and_index',IF(n=103,'completed',IF(n=102,'running','pending')),IF(n>=102,1,0) FROM fixture_numbers;
SQL
export SMARTDOCS_MYSQL_DATABASE=smart_docs_upgrade
"${mysql_test[@]}" smart_docs_upgrade < "$repo_root/db/migrations/003_processing_jobs.sql"
"${mysql_test[@]}" smart_docs_upgrade < "$repo_root/db/migrations/004_document_results.sql"
# A real schema-4 publication awaiting approved vectors: upgrade must queue it,
# not fabricate vectors or rerun the already complete parser.
"${mysql_test[@]}" smart_docs_upgrade <<'SQL'
UPDATE files SET remote_ai_policy='remote_ai_allowed' WHERE id=LPAD('1',32,'0');
UPDATE file_versions SET remote_ai_approved=TRUE,remote_ai_approved_at=UTC_TIMESTAMP(6),remote_ai_approved_by=LPAD('1',32,'0'),
 processing_state='completed',parse_state='completed',keyword_state='completed',vector_state='pending'
 WHERE id=LPAD('1001',32,'0');
UPDATE processing_jobs SET state='completed' WHERE file_version_id=LPAD('1001',32,'0');
INSERT INTO document_results(version_id,source_sha256,parser_revision,result_sha256,page_count,pages_without_text,chunk_count,char_count)
 VALUES (LPAD('1001',32,'0'),REPEAT('a',64),'utf8-lines-v1',REPEAT('b',64),0,'[]',1,3);
INSERT INTO document_chunks(id,version_id,ordinal,char_start,char_end,line_start,line_end,body)
 VALUES (REPEAT('c',64),LPAD('1001',32,'0'),0,0,3,1,1,'abc');
SQL
[[ $("$repo_root/scripts/migrate.sh") == 'schema_version=16' ]]
[[ $("$repo_root/scripts/migrate.sh") == 'schema_version=16' ]]
result=$("${mysql_test[@]}" smart_docs_upgrade --execute="SELECT CONCAT(
 (SELECT COUNT(*) FROM processing_jobs WHERE state='pending'),'/',
 (SELECT COUNT(*) FROM processing_jobs WHERE state='failed' AND last_error_code='queue_full'),'/',
 (SELECT COUNT(*) FROM processing_jobs WHERE state='interrupted' AND last_error_code='service_restarted'),'/',
 (SELECT COUNT(*) FROM file_versions WHERE processing_state='failed'),'/',
 (SELECT COUNT(*) FROM file_versions WHERE processing_state='interrupted'),'/',
 (SELECT COUNT(*) FROM files WHERE current_version_id IS NOT NULL));")
[[ $result == '100/1/1/1/2/103' ]] || { printf 'migration_fixture_failed: %s\n' "$result" >&2; exit 1; }
result=$("${mysql_test[@]}" smart_docs_upgrade --execute="SELECT CONCAT(
 (SELECT COUNT(*) FROM file_versions WHERE parse_state<>processing_state),'/',
 (SELECT COUNT(*) FROM file_versions WHERE keyword_state<>'pending' AND id<>LPAD('1001',32,'0')),'/',
 (SELECT COUNT(*) FROM processing_jobs WHERE state='interrupted' AND last_error_code='results_unavailable'),'/',
 (SELECT COUNT(*) FROM document_results),'/',(SELECT COUNT(*) FROM document_chunks));")
[[ $result == '0/0/1/1/1' ]] || { printf 'migration_result_state_failed: %s\n' "$result" >&2; exit 1; }
result=$("${mysql_test[@]}" smart_docs_upgrade --execute="SELECT CONCAT(
 (SELECT COUNT(*) FROM processing_jobs WHERE task_type='vectorize' AND state='pending' AND file_version_id=LPAD('1001',32,'0')),'/',
 (SELECT COUNT(*) FROM document_embeddings),'/',(SELECT COUNT(*) FROM embedding_batches));")
[[ $result == '1/0/0' ]] || { printf 'migration_vector_state_failed: %s\n' "$result" >&2; exit 1; }
printf '%s\n' 'PASS m2_migration_preserves_103_files_without_fabricating_results'
result=$("${mysql_test[@]}" smart_docs_upgrade --execute="SELECT CONCAT(
 (SELECT COUNT(*) FROM agent_tasks),'/',(SELECT COUNT(*) FROM agent_task_versions),'/',
 (SELECT COUNT(*) FROM agent_queue_guard WHERE id=1));")
[[ $result == '0/0/1' ]] || { printf 'migration_agent_state_failed: %s\n' "$result" >&2; exit 1; }
printf '%s\n' 'PASS m3_agent_migration_does_not_invent_tasks_or_consent'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM agent_runs;') == 0 ]]
printf '%s\n' 'PASS m3_run_migration_does_not_invent_execution'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM agent_tool_calls;') == 0 ]]
printf '%s\n' 'PASS m3_tool_audit_migration_does_not_invent_history'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT (SELECT COUNT(*) FROM archive_policies)+(SELECT COUNT(*) FROM archive_plans)+(SELECT COUNT(*) FROM archive_decisions);') == 0 ]]
printf '%s\n' 'PASS m3_archive_migration_does_not_invent_policy_or_plans'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM handover_checklists;') == 0 ]]
printf '%s\n' 'PASS m3_handover_migration_does_not_invent_checklist_or_approval'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM agent_tool_calls WHERE source_json IS NOT NULL OR model_call IS NOT NULL;') == 0 ]]
printf '%s\n' 'PASS m3_source_migration_does_not_invent_model_context'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM agent_tasks WHERE handover_context IS NOT NULL;') == 0 ]]
printf '%s\n' 'PASS m3_checklist_context_migration_does_not_invent_selection'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT (SELECT COUNT(*) FROM handover_reports)+(SELECT COUNT(*) FROM agent_runs WHERE report_operation_id IS NOT NULL);') == 0 ]]
printf '%s\n' 'PASS m3_report_migration_does_not_invent_reports_or_operations'
[[ $("${mysql_test[@]}" smart_docs_upgrade --execute='SELECT COUNT(*) FROM agent_tasks WHERE archive_context IS NOT NULL;') == 0 ]]
printf '%s\n' 'PASS m3_archive_context_migration_does_not_invent_selection'
