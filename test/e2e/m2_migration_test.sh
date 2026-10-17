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
  SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<102
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
       IF(n=102,'processing','pending') FROM fixture_numbers;
UPDATE files f JOIN file_versions v ON v.file_id=f.id SET f.current_version_id=v.id;
INSERT INTO processing_jobs(id,file_version_id,task_type,state,attempt_count)
SELECT LPAD(n+3000,32,'0'),LPAD(n+1000,32,'0'),'parse_and_index',IF(n=102,'running','pending'),IF(n=102,1,0) FROM fixture_numbers;
SQL
export SMARTDOCS_MYSQL_DATABASE=smart_docs_upgrade
[[ $("$repo_root/scripts/migrate.sh") == 'schema_version=3' ]]
[[ $("$repo_root/scripts/migrate.sh") == 'schema_version=3' ]]
result=$("${mysql_test[@]}" smart_docs_upgrade --execute="SELECT CONCAT(
 (SELECT COUNT(*) FROM processing_jobs WHERE state='pending'),'/',
 (SELECT COUNT(*) FROM processing_jobs WHERE state='failed' AND last_error_code='queue_full'),'/',
 (SELECT COUNT(*) FROM processing_jobs WHERE state='interrupted' AND last_error_code='service_restarted'),'/',
 (SELECT COUNT(*) FROM file_versions WHERE processing_state='failed'),'/',
 (SELECT COUNT(*) FROM file_versions WHERE processing_state='interrupted'),'/',
 (SELECT COUNT(*) FROM files WHERE current_version_id IS NOT NULL));")
[[ $result == '100/1/1/1/1/102' ]] || { printf 'migration_fixture_failed: %s\n' "$result" >&2; exit 1; }
printf '%s\n' 'PASS m2_migration_preserves_102_files_bounds_pending_and_interrupts_running'
