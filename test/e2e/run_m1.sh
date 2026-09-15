#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
build_jobs=${SMARTDOCS_BUILD_JOBS:-1}
if [[ ! $build_jobs =~ ^[1-9][0-9]*$ ]]; then
    printf 'invalid_build_parallelism: %s\n' "$build_jobs" >&2
    exit 1
fi
online_cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1\n')
if [[ ! $online_cpus =~ ^[1-9][0-9]*$ ]]; then
    online_cpus=1
fi
safe_build_jobs=$online_cpus
if (( safe_build_jobs > 2 )); then
    safe_build_jobs=2
fi
if (( build_jobs > safe_build_jobs )); then
    printf 'unsafe_build_parallelism: requested %s; safe maximum is %s\n' \
        "$build_jobs" "$safe_build_jobs" >&2
    exit 1
fi
test_root=$(mktemp -d /tmp/smartdocs-m1.XXXXXX)
mysql_root="$test_root/mysql"
data_dir="$mysql_root/data"
socket_path="$mysql_root/mysql.sock"
pid_file="$mysql_root/mysql.pid"
mysql_log="$test_root/logs/mysql.log"
driver_log_dir="$test_root/logs/servers"
scratch_evidence="$test_root/evidence"
mysql_pid=''
normal_server_pid=''

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n $mysql_pid ]] && kill -0 "$mysql_pid" 2>/dev/null; then
        kill "$mysql_pid" 2>/dev/null || true
        wait "$mysql_pid" 2>/dev/null || true
    fi
    if [[ -n $normal_server_pid ]] && kill -0 "$normal_server_pid" 2>/dev/null; then
        kill "$normal_server_pid" 2>/dev/null || true
        wait "$normal_server_pid" 2>/dev/null || true
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

for program in mysqld mysql mysqladmin python3 node git make cat; do
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

python3 -B "$repo_root/test/e2e/m1_harness_self_test.py"
build_head=$(git -C "$repo_root" rev-parse HEAD)
if [[ -n $(git -C "$repo_root" status --porcelain --untracked-files=normal) ]]; then
    build_dirty=true
else
    build_dirty=false
fi
make -C "$repo_root" clean
make -C "$repo_root" -j"$build_jobs" \
    server admin test-server bin/smartdocs_tests
for artifact in bin/server bin/smartdocs-admin bin/smartdocs_test_server \
    bin/smartdocs_tests; do
    [[ -x "$repo_root/$artifact" ]] || {
        printf 'build_artifact_missing: %s\n' "$artifact" >&2
        exit 1
    }
done
if [[ -n $(git -C "$repo_root" status --porcelain --untracked-files=normal) ]]; then
    current_dirty=true
else
    current_dirty=false
fi
if [[ $(git -C "$repo_root" rev-parse HEAD) != "$build_head" ]] ||
    [[ $current_dirty != "$build_dirty" ]]; then
    printf 'checkout_changed_during_build\n' >&2
    exit 1
fi

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
export SMARTDOCS_TEST_MYSQL=1
unset SMARTDOCS_FAULT_POINT

"$repo_root/scripts/migrate.sh" >/dev/null
set +e
"$repo_root/bin/smartdocs_tests" \
    >"$scratch_evidence/cpp-suite.txt" 2>&1
cpp_suite_status=$?
python3 -B "$repo_root/test/e2e/ui_contract_test.py" \
    >"$scratch_evidence/ui-contract.txt" 2>&1
ui_contract_status=$?
node "$repo_root/test/e2e/ui_behavior_test.mjs" \
    >"$scratch_evidence/ui-behavior.txt" 2>&1
ui_behavior_status=$?
python3 -B "$repo_root/test/e2e/m1_interrupt_test.py" \
    --validate-supporting-suites \
    --cpp-suite-output "$scratch_evidence/cpp-suite.txt" \
    --ui-contract-output "$scratch_evidence/ui-contract.txt" \
    --ui-behavior-output "$scratch_evidence/ui-behavior.txt" \
    >"$scratch_evidence/supporting-suites.json"
suite_validation_status=$?
set -e
if (( cpp_suite_status != 0 || ui_contract_status != 0 ||
      ui_behavior_status != 0 || suite_validation_status != 0 )); then
    printf 'supporting_suite_status: cpp=%s ui_contract=%s ui_behavior=%s validation=%s\n' \
        "$cpp_suite_status" "$ui_contract_status" "$ui_behavior_status" \
        "$suite_validation_status" >&2
    for suite_output in "$scratch_evidence/cpp-suite.txt" \
        "$scratch_evidence/ui-contract.txt" \
        "$scratch_evidence/ui-behavior.txt"; do
        printf '%s:\n' "${suite_output##*/}" >&2
        cat "$suite_output" >&2
    done
    exit 1
fi
SMARTDOCS_LISTEN_ADDRESS=127.0.0.1 SMARTDOCS_PORT=13161 \
    "$repo_root/bin/server" >>"$test_root/logs/server.log" 2>&1 &
normal_server_pid=$!
sleep 0.1
if ! kill -0 "$normal_server_pid" 2>/dev/null; then
    printf 'normal_server_start_failed\n' >&2
    exit 1
fi
kill "$normal_server_pid"
wait "$normal_server_pid" 2>/dev/null || true
normal_server_pid=''
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
    --normal-server-bin "$repo_root/bin/server" \
    --admin-bin "$repo_root/bin/smartdocs-admin" \
    --log-dir "$driver_log_dir/interrupt" \
    --context-file "$scratch_evidence/context.json" \
    --http-evidence "$scratch_evidence/http.json" \
    --output "$repo_root/docs/evidence/m1/latest/results.json" \
    --build-head "$build_head" \
    --build-dirty "$build_dirty" \
    --supporting-suites "$scratch_evidence/supporting-suites.json"

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
