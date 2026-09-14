#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
migration_dir="$repo_root/db/migrations"

require_nonempty() {
    local name=$1
    if [[ -z ${!name:-} ]]; then
        printf 'config_missing: %s is required\n' "$name" >&2
        exit 2
    fi
}

require_nonempty SMARTDOCS_MYSQL_HOST
require_nonempty SMARTDOCS_MYSQL_DATABASE
require_nonempty SMARTDOCS_MYSQL_USER
require_nonempty SMARTDOCS_MYSQL_PASSWORD

if [[ ! ${SMARTDOCS_MYSQL_DATABASE} =~ ^[A-Za-z0-9_]+$ ]]; then
    printf 'config_invalid: SMARTDOCS_MYSQL_DATABASE must contain only letters, digits, or underscore\n' >&2
    exit 2
fi

mysql_args=(
    --batch
    --skip-column-names
    --raw
    --host="$SMARTDOCS_MYSQL_HOST"
    --user="$SMARTDOCS_MYSQL_USER"
)
if [[ -n ${SMARTDOCS_MYSQL_SOCKET:-} ]]; then
    mysql_args+=(--protocol=SOCKET --socket="$SMARTDOCS_MYSQL_SOCKET")
else
    require_nonempty SMARTDOCS_MYSQL_PORT
    if [[ ! ${SMARTDOCS_MYSQL_PORT} =~ ^[0-9]+$ ]] ||
       (( SMARTDOCS_MYSQL_PORT < 1 || SMARTDOCS_MYSQL_PORT > 65535 )); then
        printf 'config_invalid: SMARTDOCS_MYSQL_PORT must be between 1 and 65535\n' >&2
        exit 2
    fi
    mysql_args+=(--protocol=TCP --port="$SMARTDOCS_MYSQL_PORT")
fi

export MYSQL_PWD=$SMARTDOCS_MYSQL_PASSWORD

query() {
    mysql "${mysql_args[@]}" "$SMARTDOCS_MYSQL_DATABASE" \
        --execute="$1"
}

m1_tables="'schema_migrations','users','auth_sessions','projects','project_members','directories','files','file_versions','upload_tasks','upload_parts','processing_jobs','audit_records'"
existing_count=$(query "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name IN ($m1_tables);")
has_version_table=$(query "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='schema_migrations';")
current_version=0
if [[ $has_version_table == 1 ]]; then
    current_version=$(query "SELECT COALESCE(MAX(version), 0) FROM schema_migrations;")
fi

if (( current_version == 0 && existing_count != 0 )); then
    printf '%s\n' 'partial_schema: M1 tables exist without migration version 1; restore or recreate this application database' >&2
    exit 3
fi

latest_version=0
shopt -s nullglob
migrations=("$migration_dir"/[0-9][0-9][0-9]_*.sql)
if (( ${#migrations[@]} == 0 )); then
    printf 'migration_missing: no numbered migrations found in %s\n' "$migration_dir" >&2
    exit 4
fi

for migration in "${migrations[@]}"; do
    file_name=${migration##*/}
    raw_version=${file_name%%_*}
    version=$((10#$raw_version))
    latest_version=$version
    if (( version <= current_version )); then
        continue
    fi
    if (( version != current_version + 1 )); then
        printf 'migration_gap: expected version %d but found %d\n' \
            "$((current_version + 1))" "$version" >&2
        exit 4
    fi
    mysql "${mysql_args[@]}" "$SMARTDOCS_MYSQL_DATABASE" < "$migration"
    current_version=$(query "SELECT COALESCE(MAX(version), 0) FROM schema_migrations;")
    if (( current_version != version )); then
        printf 'migration_failed: %s did not record version %d\n' \
            "$file_name" "$version" >&2
        exit 5
    fi
done

confirmed_version=$(query "SELECT COALESCE(MAX(version), 0) FROM schema_migrations;")
if (( confirmed_version != latest_version )); then
    printf 'migration_failed: expected schema version %d, found %d\n' \
        "$latest_version" "$confirmed_version" >&2
    exit 5
fi
printf 'schema_version=%d\n' "$confirmed_version"
