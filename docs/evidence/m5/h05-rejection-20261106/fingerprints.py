"""Bind the already tested product plus the live benchmark harness and frozen inputs."""
import hashlib
from pathlib import Path
import re

runtime=Path('/tmp/smartdocs-safe.h4SzvX/repo')
base=Path('docs/evidence/m5')
paths=set()
for name in ('query-budget-20261106/live-postflight.txt','report-rejection-20261106/postflight.txt'):
    paths.update(re.findall(r'^[0-9a-f]{64} (.+)$',(base/name).read_text(),re.M))
for name in sorted(paths):
    path=Path(name)
    raw=path.read_bytes()
    if not path.is_absolute(): assert raw==(runtime/path).read_bytes(),name
    print(hashlib.sha256(raw).hexdigest(),name)
print(f'PASS: {len(paths)} runtime/source/fixture entries bound; no key or network access')
