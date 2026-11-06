#!/usr/bin/env bash
# Host-specific isolated restore; no model API/key or production DB access.
set -euo pipefail
mode=${1:?keyword or vectors}
case "$mode" in
    keyword) options=(); test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0 ;;
    vectors) options=(--vectors); test -S "${SMARTDOCS_ENCODER_SOCKET:?isolated offline socket required}" ;;
    *) exit 2 ;;
esac
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
systemd-run --unit="smartdocs-restore-inspection-$mode-20261107" --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p RuntimeMaxSec=780 -p TimeoutStopSec=5 -p KillMode=control-group -p IPAddressDeny=any -p IPAddressAllow=localhost --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python --setenv="SMARTDOCS_ENCODER_SOCKET=${SMARTDOCS_ENCODER_SOCKET:-}" /bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_backup_restore_test.py "${options[@]}"
