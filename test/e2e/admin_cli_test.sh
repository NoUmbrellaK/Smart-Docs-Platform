#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d /tmp/smartdocs-admin-test.XXXXXX)
cleanup() {
    local status=$?
    trap - EXIT
    case "$test_root" in
        /tmp/smartdocs-admin-test.*) rm -rf -- "$test_root" ;;
        *) printf 'cleanup_refused: unexpected path %s\n' "$test_root" >&2 ;;
    esac
    exit "$status"
}
trap cleanup EXIT

mkdir -m 0700 "$test_root/storage"
mkdir -m 0700 "$test_root/storage/objects" "$test_root/storage/staging"
export SMARTDOCS_STORAGE_ROOT="$test_root/storage"
export SMARTDOCS_PASSWORD_ITERATIONS=1000
password='development-password'

printf '%s\n' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username admin --password-stdin \
    >"$test_root/create.out" 2>"$test_root/create.err"
if ! rg -q '^created_user_id=[0-9a-f]{32}$' "$test_root/create.out"; then
    printf '%s\n' 'admin_cli_failed: successful creation did not return an ID' >&2
    exit 1
fi
if rg -Fq "$password" "$test_root/create.out" "$test_root/create.err"; then
    printf '%s\n' 'admin_cli_failed: password appeared in command output' >&2
    exit 1
fi

set +e
printf '%s\n' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username admin --password-stdin \
    >"$test_root/duplicate.out" 2>"$test_root/duplicate.err"
duplicate_status=$?
set -e
if (( duplicate_status != 2 )) ||
   ! rg -q '^username_conflict:' "$test_root/duplicate.err"; then
    printf '%s\n' 'admin_cli_failed: duplicate username contract was not met' >&2
    exit 1
fi

set +e
printf '%s\n' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username missing-flag \
    >"$test_root/flag.out" 2>"$test_root/flag.err"
flag_status=$?
printf '%s' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username no-newline --password-stdin \
    >"$test_root/newline.out" 2>"$test_root/newline.err"
newline_status=$?
printf '%s\nextra\n' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username two-lines --password-stdin \
    >"$test_root/two-lines.out" 2>"$test_root/two-lines.err"
two_lines_status=$?
printf '%s\n' "$password" | "$repo_root/bin/smartdocs-admin" \
    create-user --username ab --password-stdin \
    >"$test_root/username.out" 2>"$test_root/username.err"
username_status=$?
printf '%s\n' 'short' | "$repo_root/bin/smartdocs-admin" \
    create-user --username short-password --password-stdin \
    >"$test_root/password.out" 2>"$test_root/password.err"
password_status=$?
set -e
if (( flag_status != 2 || newline_status != 2 || two_lines_status != 2 ||
      username_status != 2 || password_status != 2 )); then
    printf '%s\n' 'admin_cli_failed: password input validation was not enforced' >&2
    exit 1
fi
if rg -Fq "$password" "$test_root"/*.out "$test_root"/*.err; then
    printf '%s\n' 'admin_cli_failed: password appeared in failure output' >&2
    exit 1
fi

printf '%s\n' 'PASS admin_cli_password_stdin_and_duplicate_contract'
