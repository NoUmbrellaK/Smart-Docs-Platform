#!/usr/bin/env bash
# Run from the worktree; no model, credentials, production database, or external network.
set -euo pipefail
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
case "${1:-}" in
  build) command=(make -j1 bin/server bin/smartdocs-admin) ;;
  green) command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_handover_report_test.py) ;;
  writeaudit) command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_write_tool_audit_test.py) ;;
  readaudit) command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_tool_audit_test.py) ;;
  mcp|mcp-fixed) command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_report_mcp_test.py) ;;
  red) command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest -v m3_handover_report_test.HandoverReportE2E.test_report_rejection_audit_classifies_without_content_or_permission_leak) ;;
  *) exit 2 ;;
esac
systemd-run --unit="smartdocs-report-rejection-$1-20261106" --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group -p IPAddressDeny=any -p IPAddressAllow=localhost --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e "${command[@]}"
