"""Bind schema17, product binaries and the extended restore test to this runtime."""
import hashlib
from pathlib import Path
import runpy

bound = runpy.run_path('docs/evidence/m5/inspection-constraints-20261106/fingerprints.py')
# All same-directory imports of this test are already included by that binder.
path = Path('test/e2e/m3_backup_restore_test.py')
raw = path.read_bytes()
assert raw == (bound['runtime'] / path).read_bytes(), path
print(hashlib.sha256(raw).hexdigest(), path)
print('PASS: restore test also matches runtime')
