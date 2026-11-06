#!/usr/bin/env bash
set -euo pipefail
test "$(git rev-parse HEAD)" = 5baa273f17a8f5c9512370ed743d5b4ea26eb119
test "$(systemctl show smartdocsvectorcheck.slice -p TasksCurrent --value)" = 0
awk '/MemAvailable/ {print;exit ($2<524288)}' /proc/meminfo
python3 -B docs/evidence/m5/h05-diagnostic-20261106/fingerprints.py > docs/evidence/m5/h05-diagnostic-20261106/preflight.txt
test ! -e /tmp/smartdocs-m5-h05-diagnostic.luGADy/H05
chown nobody:nogroup /tmp/smartdocs-m5-h05-diagnostic.luGADy
chmod 0700 /tmp/smartdocs-m5-h05-diagnostic.luGADy
systemd-run --unit=smartdocs-m5-h05-diagnostic-20261106 --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group -p LoadCredential=deepseek.env:/root/.config/smart-docs/deepseek.env --working-directory=/tmp/smartdocs-safe.h4SzvX/repo --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e /bin/bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -c 'import os,pathlib,shlex,runpy,sys,re
tokens=shlex.split((pathlib.Path(os.environ["CREDENTIALS_DIRECTORY"])/"deepseek.env").read_text(),comments=True)
entries=[t.partition("=") for t in tokens if t.startswith("DEEPSEEK_API_KEY=")]
if len(entries)!=1 or not re.fullmatch(r"[!-~]{1,256}",entries[0][2]): raise SystemExit("invalid credential configuration")
os.environ["DEEPSEEK_API_KEY"]=entries[0][2]
sys.argv=["test/e2e/benchmark_cli.py",*sys.argv[1:]]
runpy.run_path(sys.argv[0],run_name="__main__")' --case H05 --live --output /tmp/smartdocs-m5-h05-diagnostic.luGADy/H05 --code-revision 5baa273f17a8f5c9512370ed743d5b4ea26eb119 --environment 'Current ECS 2vCPU 3495MiB; CPU100% MemoryHigh1200MiB Max1600MiB Swap0 IO5MB/s 200IOPS Tasks256; Python3.10.12 MCP1.30.0 HTTPX0.28.1; TXT keyword indexes no encoder; isolated schema16 MySQL C++/stdio MCP; user authorized repeated synthetic tests; execution2026-11-06; H05 fifth attempt; current committed prompts, previous wording candidate NOT restored; read-target diagnostics enabled; 48 pinned runtime entries; frozen question/checklist/expected/budgets unchanged; eleven approved synthetic versions only; metadata first page <=10, membership depends on fresh file IDs; no expected answers sent; fixed https://api.deepseek.com/chat/completions; <=6 requests x4096 output; no automatic retry or archive confirmation' --max-calls 6 --hold-seconds 0
systemctl show smartdocs-m5-h05-diagnostic-20261106.service -p Id -p ActiveState -p ExecStart -p Slice -p User -p Group -p Nice -p UMask -p NoNewPrivileges -p RuntimeMaxUSec > docs/evidence/m5/h05-diagnostic-20261106/launch-state.txt
