"""Verify tested Python sources match this checkout; no credentials or network."""
import hashlib
from pathlib import Path

runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
files = sorted(set(Path('python/smartdocs').glob('*.py')) |
               set(Path('test/mcp').glob('*.py')) |
               {Path('test/python/test_search_http.py')})
for path in files:
    content = path.read_bytes()
    assert content == (runtime/path).read_bytes(), path
    print(hashlib.sha256(content).hexdigest(), path)
print(f'PASS: {len(files)} Python source/test files match runtime')
