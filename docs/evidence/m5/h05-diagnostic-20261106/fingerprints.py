"""Bind the existing bounded runtime; no credential access or network."""
import hashlib
from pathlib import Path
import re

runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
entries = re.findall(r'^([0-9a-f]{64}) (.+)$',
    Path('docs/evidence/m5/query-budget-20261106/live-postflight.txt').read_text(), re.M)
assert len(entries) == 48
changed = set()
for old, name in entries:
    path = Path(name)
    raw = path.read_bytes()
    if not path.is_absolute():
        assert raw == (runtime / path).read_bytes(), name
    digest = hashlib.sha256(raw).hexdigest()
    if digest != old:
        changed.add(name)
    print(digest, name)
assert changed == {'python/smartdocs/agent_cli.py', 'python/smartdocs/audit_summary.py',
                   'python/smartdocs/search_http.py', 'test/mcp/test_agent_runner.py'}, changed
print('PASS: 48 entries match runtime; only committed HTTP fix/read diagnostics and associated test differ from query-budget baseline')
