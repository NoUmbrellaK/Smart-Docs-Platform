"""Pin the existing runtime against the last verified 48-entry manifest."""
import hashlib
from pathlib import Path
import re

root = Path.cwd()
runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
entries = re.findall(r'^([0-9a-f]{64}) (.+)$',
    (root / 'docs/evidence/m5/query-budget-20261106/live-postflight.txt').read_text(), re.M)
assert len(entries) == 48
changed = set()
for old, name in entries:
    path = Path(name)
    raw = (path if path.is_absolute() else root / path).read_bytes()
    if not path.is_absolute():
        assert raw == (runtime / path).read_bytes(), name
    digest = hashlib.sha256(raw).hexdigest()
    if digest != old:
        changed.add(name)
    print(digest, name)
assert changed == {'python/smartdocs/agent_runner.py'}, changed
print('48 entries verified; only agent_runner.py changed; fixed inputs and budgets unchanged')
