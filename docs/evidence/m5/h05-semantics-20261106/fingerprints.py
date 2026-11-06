"""Read-only check of candidate against the previous 47-entry runtime baseline."""
import hashlib
from pathlib import Path
import re

root = Path.cwd()
runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
baseline = root / 'docs/evidence/m5/archive-capture-20261106/postflight.txt'
entries = re.findall(r'^([0-9a-f]{64}) (.+)$', baseline.read_text(), re.M)
assert len(entries) == 47
changed = []
for old, name in entries:
    path = Path(name)
    actual = path if path.is_absolute() else root / path
    blob = actual.read_bytes()
    if not path.is_absolute():
        assert blob == (runtime / path).read_bytes(), name
    digest = hashlib.sha256(blob).hexdigest()
    if digest != old:
        changed.append(name)
    print(digest, name)
assert changed == ['python/smartdocs/agent_runner.py'], changed
patch = root / 'docs/evidence/m5/h05-semantics-20261106/candidate.patch'
print('candidate.patch SHA256:', hashlib.sha256(patch.read_bytes()).hexdigest())
print('47 entries checked; only agent_runner differs from b6670d6 baseline')
