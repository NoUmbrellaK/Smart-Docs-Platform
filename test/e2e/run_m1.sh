#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d /tmp/smartdocs-m1.XXXXXX)
mysql_root="$test_root/mysql"
data_dir="$mysql_root/data"
socket_path="$mysql_root/mysql.sock"
pid_file="$mysql_root/mysql.pid"
mysql_log="$test_root/logs/mysql.log"
driver_log_dir="$test_root/logs/servers"
scratch_evidence="$test_root/evidence"
mysql_pid=''

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n $mysql_pid ]] && kill -0 "$mysql_pid" 2>/dev/null; then
        kill "$mysql_pid" 2>/dev/null || true
        wait "$mysql_pid" 2>/dev/null || true
    fi
    case "$test_root" in
        /tmp/smartdocs-m1.*) rm -rf -- "$test_root" ;;
        *) printf 'cleanup_refused: unexpected temporary path\n' >&2 ;;
    esac
    exit "$status"
}
trap cleanup EXIT INT TERM

mkdir -m 0700 "$mysql_root" "$data_dir" "$test_root/storage" \
    "$test_root/storage/objects" "$test_root/storage/staging" \
    "$test_root/logs" "$driver_log_dir" "$scratch_evidence"

for program in mysqld mysql mysqladmin python3 git make; do
    command -v "$program" >/dev/null || {
        printf 'missing_dependency: %s\n' "$program" >&2
        exit 1
    }
done
for artifact in test/fixtures/m1/plain.txt test/fixtures/m1/sample.pdf; do
    [[ -e "$repo_root/$artifact" ]] || {
        printf 'missing_artifact: %s\n' "$artifact" >&2
        exit 1
    }
done

build_head=$(git -C "$repo_root" rev-parse HEAD)
if [[ -n $(git -C "$repo_root" status --porcelain --untracked-files=normal) ]]; then
    build_dirty=true
else
    build_dirty=false
fi
python3 -B "$repo_root/test/e2e/m1_harness_self_test.py"
make -C "$repo_root" clean
make -C "$repo_root" -j"${SMARTDOCS_BUILD_JOBS:-4}" \
    server admin test-server
for artifact in bin/server bin/smartdocs-admin bin/smartdocs_test_server; do
    [[ -x "$repo_root/$artifact" ]] || {
        printf 'build_artifact_missing: %s\n' "$artifact" >&2
        exit 1
    }
done

user_args=()
if [[ $(id -u) == 0 ]]; then
    user_args+=(--user=root)
fi
mysqld --no-defaults --initialize-insecure --datadir="$data_dir" \
    "${user_args[@]}" >>"$mysql_log" 2>&1
mysqld --no-defaults --datadir="$data_dir" --socket="$socket_path" \
    --pid-file="$pid_file" --skip-networking --log-error="$mysql_log" \
    "${user_args[@]}" &
mysql_pid=$!

ready=0
for _ in $(seq 1 200); do
    if ! kill -0 "$mysql_pid" 2>/dev/null; then
        printf 'mysql_start_failed: isolated server exited early\n' >&2
        exit 1
    fi
    if mysqladmin --protocol=SOCKET --socket="$socket_path" --user=root \
        ping >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.1
done
if (( ready == 0 )); then
    printf 'mysql_start_failed: isolated server did not become ready\n' >&2
    exit 1
fi

mysql --protocol=SOCKET --socket="$socket_path" --user=root <<'SQL'
CREATE DATABASE smart_docs_m1
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'smart_docs_m1'@'localhost'
  IDENTIFIED BY 'm1-database-test-only';
GRANT ALL PRIVILEGES ON smart_docs_m1.*
  TO 'smart_docs_m1'@'localhost';
SQL

export SMARTDOCS_MYSQL_HOST=localhost
export SMARTDOCS_MYSQL_PORT=3306
export SMARTDOCS_MYSQL_DATABASE=smart_docs_m1
export SMARTDOCS_MYSQL_USER=smart_docs_m1
export SMARTDOCS_MYSQL_PASSWORD=m1-database-test-only
export SMARTDOCS_MYSQL_SOCKET=$socket_path
export SMARTDOCS_STORAGE_ROOT="$test_root/storage"
export SMARTDOCS_MAX_FILE_BYTES=1048576
export SMARTDOCS_CHUNK_BYTES=16
export SMARTDOCS_MAX_JSON_BYTES=65536
export SMARTDOCS_MYSQL_POOL_SIZE=4
export SMARTDOCS_THREAD_COUNT=4
export SMARTDOCS_PASSWORD_ITERATIONS=1000
export SMARTDOCS_LOG_LEVEL=0
export SMARTDOCS_E2E_PASSWORD=m1-account-test-only
unset SMARTDOCS_FAULT_POINT

"$repo_root/scripts/migrate.sh" >/dev/null
python3 -B "$repo_root/test/e2e/m1_http_test.py" \
    --server-bin "$repo_root/bin/smartdocs_test_server" \
    --admin-bin "$repo_root/bin/smartdocs-admin" \
    --log-dir "$driver_log_dir/http" \
    --context-file "$scratch_evidence/context.json" \
    --evidence-file "$scratch_evidence/http.json" \
    --plain "$repo_root/test/fixtures/m1/plain.txt" \
    --pdf "$repo_root/test/fixtures/m1/sample.pdf"
python3 -B "$repo_root/test/e2e/m1_interrupt_test.py" \
    --server-bin "$repo_root/bin/smartdocs_test_server" \
    --admin-bin "$repo_root/bin/smartdocs-admin" \
    --log-dir "$driver_log_dir/interrupt" \
    --context-file "$scratch_evidence/context.json" \
    --http-evidence "$scratch_evidence/http.json" \
    --output "$repo_root/docs/evidence/m1/latest/results.json" \
    --build-head "$build_head" \
    --build-dirty "$build_dirty"

python3 -B - "$repo_root/docs/evidence/m1/latest/results.json" <<'PY'
import json
import pathlib
import sys

result = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
summary = result["summary"]
print("M1 evidence: "
      f"HTTP {summary['http_scenarios_passed']}/{summary['http_scenarios_total']}; "
      f"interruptions {summary['interruption_rounds_passed']}/"
      f"{summary['interruption_rounds_total']}; "
      f"assertions {summary['http_assertions'] + summary['interruption_assertions']}")
PY
