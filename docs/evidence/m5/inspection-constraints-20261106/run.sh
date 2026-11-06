#!/usr/bin/env bash
# Current ECS, serial isolated fixtures; no model, credentials or production changes.
set -euo pipefail
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
case "${1:-}" in
  build) command=(make -j1 bin/server bin/smartdocs-admin) ;;
  green|green-fixed) test_file=m3_inspection_constraints_test.py ;;
  tasklist) test_file=m3_task_list_test.py ;;
  reports) test_file=m3_handover_report_test.py ;;
  writeaudit) test_file=m3_write_tool_audit_test.py ;;
  reportmcp) test_file=m3_report_mcp_test.py ;;
  mcp) test_file=m3_inspection_mcp_test.py ;;
  browser|browser-fixed)
    test_file=m3_inspection_browser_test.py
    : "${SMARTDOCS_INSPECTION_BROWSER_OUTPUT:?fresh screenshot output directory required}"
    ;;
  browser-probe)
    : "${SMARTDOCS_INSPECTION_BROWSER_OUTPUT:?fresh diagnostic output directory required}"
    command=(/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B /tmp/smartdocs-inspection-browser-probe-20261106.py)
    ;;
  python) command=(/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py') ;;
  python-red|python-red-fixed) command=(/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p test_agent_runner.py -k inspection -v) ;;
  ui) command=(/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/agent_ui_test.mjs) ;;
  ui-red|ui-red-fixed) command=(/tmp/smartdocs-safe.h4SzvX/bin/node --test --test-name-pattern=inspection test/e2e/agent_ui_test.mjs) ;;
  *) exit 2 ;;
esac
if [[ -n ${test_file:-} ]]; then
  command=(/bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B "test/e2e/$test_file")
fi
extra=()
if [[ ${1:-} == browser* ]]; then
  extra+=("--setenv=SMARTDOCS_INSPECTION_BROWSER_OUTPUT=$SMARTDOCS_INSPECTION_BROWSER_OUTPUT")
  extra+=(--setenv=SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome)
  extra+=(--setenv=SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver)
fi
systemd-run --unit="smartdocs-inspection-$1-20261106" --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group -p IPAddressDeny=any -p IPAddressAllow=localhost --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e "${extra[@]}" "${command[@]}"
