"""Verify the existing runtime changed only in the bounded provider fix and its tests."""
import hashlib
from pathlib import Path
import re

root = Path.cwd()
runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
entries = re.findall(r'^([0-9a-f]{64}) (.+)$', (root / 'docs/evidence/m5/s01-live-20261106/postflight.txt').read_text(), re.M)
assert len(entries) == 48
changed = set()
for previous, name in entries:
    path = Path(name)
    source = path if path.is_absolute() else root / path
    raw = source.read_bytes()
    if not path.is_absolute():
        assert raw == (runtime / path).read_bytes(), name
    digest = hashlib.sha256(raw).hexdigest()
    if digest != previous:
        changed.add(name)
    print(digest, name)
assert changed == {'python/smartdocs/deepseek.py', 'test/mcp/test_deepseek.py', 'test/mcp/test_agent_runner.py'}, changed
print('48 runtime entries checked; only provider cap and two tests changed; frozen inputs unchanged')
