#!/usr/bin/env bash
# Offline characterization only; no model or credential access.
set -euo pipefail
test "$(git rev-parse HEAD)" = 910af8c2182363f9c1ef10109810b257c0f73866
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
python3 -B docs/evidence/m5/h05-rejection-20261106/fingerprints.py > docs/evidence/m5/inspection-boundary-20261106/preflight.txt
cmp docs/evidence/m5/h05-rejection-20261106/postflight.txt docs/evidence/m5/inspection-boundary-20261106/preflight.txt
cmp docs/evidence/m5/inspection-boundary-20261106/probe.py /tmp/smartdocs-safe.h4SzvX/repo/test/e2e/m5_inspection_boundary_probe.py
systemd-run --unit=smartdocs-inspection-boundary-20261106 --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group -p IPAddressDeny=any -p IPAddressAllow=localhost --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e /bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m5_inspection_boundary_probe.py
