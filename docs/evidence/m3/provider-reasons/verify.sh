#!/usr/bin/env bash
set -euo pipefail
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/*_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
scripts/test-with-mysql.sh -- /bin/bash -c './bin/smartdocs_tests --filter=m3_ && /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_agent_http_test.py'
# Independent E2E programs each create their own fixed administrator fixture.
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_contract_matrix_test.py
