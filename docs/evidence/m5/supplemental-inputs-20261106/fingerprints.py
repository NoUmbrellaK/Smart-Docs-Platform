"""Pin the supplemental test harness; product and original fixtures must not change."""
import hashlib
from pathlib import Path
import re

root = Path.cwd()
runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
baseline = root / 'docs/evidence/m5/h05-semantics-20261106/postflight.txt'
entries = re.findall(r'^([0-9a-f]{64}) (.+)$', baseline.read_text(), re.M)
assert len(entries) == 47
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
assert changed == {'test/e2e/' + name for name in ('benchmark_review.py', 'benchmark_capture.py',
    'benchmark_execute.py', 'benchmark_cli.py', 'm5_benchmark_cli_test.py')}, changed
name = 'test/fixtures/agent-p0/supplemental-tasks.json'
assert (root / name).read_bytes() == (runtime / name).read_bytes()
print(hashlib.sha256((root / name).read_bytes()).hexdigest(), name)
print('48 runtime entries checked; only five test entrypoints changed; one new fixed supplemental manifest')
