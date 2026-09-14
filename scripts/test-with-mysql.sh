#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} != -- ]] || (( $# < 2 )); then
    printf 'usage: %s -- command [args...]\n' "$0" >&2
    exit 2
fi
shift

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d /tmp/smartdocs-mysql.XXXXXX)
data_dir="$test_root/data"
socket_path="$test_root/mysql.sock"
pid_file="$test_root/mysql.pid"
error_log="$test_root/mysql.log"
server_pid=''

cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if [[ -n $server_pid ]] && kill -0 "$server_pid" 2>/dev/null; then
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
    case "$test_root" in
        /tmp/smartdocs-mysql.*) rm -rf -- "$test_root" ;;
        *) printf 'cleanup_refused: unexpected temporary path %s\n' "$test_root" >&2 ;;
    esac
    exit "$status"
}
trap cleanup EXIT INT TERM

mkdir -m 0700 "$data_dir"
user_args=()
if [[ $(id -u) == 0 ]]; then
    user_args+=(--user=root)
fi

mysqld --no-defaults --initialize-insecure --datadir="$data_dir" \
    "${user_args[@]}"
mysqld --no-defaults --datadir="$data_dir" --socket="$socket_path" \
    --pid-file="$pid_file" --skip-networking --log-error="$error_log" \
    "${user_args[@]}" &
server_pid=$!

ready=0
for _ in $(seq 1 200); do
    if ! kill -0 "$server_pid" 2>/dev/null; then
        printf '%s\n' 'mysql_start_failed: isolated server exited early' >&2
        sed -n '1,160p' "$error_log" >&2 || true
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
    printf '%s\n' 'mysql_start_failed: isolated server did not become ready' >&2
    sed -n '1,160p' "$error_log" >&2 || true
    exit 1
fi

mysql --protocol=SOCKET --socket="$socket_path" --user=root <<'SQL'
CREATE DATABASE smart_docs_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'smart_docs_test'@'localhost'
  IDENTIFIED BY 'test-only-password';
GRANT ALL PRIVILEGES ON smart_docs_test.*
  TO 'smart_docs_test'@'localhost';
SQL

export SMARTDOCS_TEST_MYSQL=1
export SMARTDOCS_MYSQL_HOST=localhost
export SMARTDOCS_MYSQL_PORT=3306
export SMARTDOCS_MYSQL_DATABASE=smart_docs_test
export SMARTDOCS_MYSQL_USER=smart_docs_test
export SMARTDOCS_MYSQL_PASSWORD=test-only-password
export SMARTDOCS_MYSQL_SOCKET=$socket_path

"$repo_root/scripts/migrate.sh"

replay_output=$("$repo_root/scripts/migrate.sh")
if [[ $replay_output != 'schema_version=2' ]]; then
    printf 'migration_replay_failed: unexpected result %s\n' "$replay_output" >&2
    exit 1
fi

mysql --protocol=SOCKET --socket="$socket_path" --user=root <<'SQL'
CREATE DATABASE smart_docs_partial
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON smart_docs_partial.*
  TO 'smart_docs_test'@'localhost';
CREATE TABLE smart_docs_partial.users (id INT PRIMARY KEY) ENGINE=InnoDB;
SQL

export SMARTDOCS_MYSQL_DATABASE=smart_docs_partial
set +e
partial_output=$("$repo_root/scripts/migrate.sh" 2>&1)
partial_status=$?
set -e
if (( partial_status == 0 )) || [[ $partial_output != partial_schema:* ]]; then
    printf 'partial_schema_test_failed: %s\n' "$partial_output" >&2
    exit 1
fi
export SMARTDOCS_MYSQL_DATABASE=smart_docs_test

"$@"
