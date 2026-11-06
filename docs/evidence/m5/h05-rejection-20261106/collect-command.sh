#!/usr/bin/env bash
set -euo pipefail
case "$(systemctl show smartdocs-m5-h05-rejection-20261106.service -p ActiveState --value)" in inactive|failed) ;; *) exit 1;; esac
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
runuser -u nobody -- env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m smartdocs.audit_summary /tmp/smartdocs-m5-h05-rejection.9mjMWF/H05/initial/audit.jsonl > docs/evidence/m5/h05-rejection-20261106/audit-summary.json
runuser -u nobody -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B /tmp/smartdocs-safe.h4SzvX/repo/test/e2e/benchmark_review.py prepare /tmp/smartdocs-m5-h05-rejection.9mjMWF/H05/initial/snapshot.json /tmp/smartdocs-m5-h05-rejection.9mjMWF/H05/review.json
python3 -B docs/evidence/m5/h05-rejection-20261106/inspect.py > docs/evidence/m5/h05-rejection-20261106/result.json
cp /tmp/smartdocs-m5-h05-rejection.9mjMWF/H05/initial/audit.jsonl docs/evidence/m5/h05-rejection-20261106/trace.jsonl
chmod 0644 docs/evidence/m5/h05-rejection-20261106/trace.jsonl
python3 -B docs/evidence/m5/h05-rejection-20261106/fingerprints.py > docs/evidence/m5/h05-rejection-20261106/postflight.txt
cmp docs/evidence/m5/h05-rejection-20261106/preflight.txt docs/evidence/m5/h05-rejection-20261106/postflight.txt
systemctl show smartdocsvectorcheck.slice -p TasksCurrent -p CPUQuotaPerSecUSec -p MemoryHigh -p MemoryMax -p MemorySwapMax -p TasksMax -p IOReadBandwidthMax -p IOWriteBandwidthMax -p IOReadIOPSMax -p IOWriteIOPSMax > docs/evidence/m5/h05-rejection-20261106/resource-limits.txt
{
  journalctl -u smartdocs-m5-h05-rejection-20261106.service --no-pager -o cat
  systemctl show smartdocs-m5-h05-rejection-20261106.service -p LoadState -p ActiveState -p Result -p ExecMainStatus
  systemctl show smartdocsvectorcheck.slice -p TasksCurrent
  cat /sys/fs/cgroup/smartdocsvectorcheck.slice/memory.events
  awk '/MemAvailable/ {print}' /proc/meminfo
  systemctl is-active mysql
} > docs/evidence/m5/h05-rejection-20261106/live-status.txt
