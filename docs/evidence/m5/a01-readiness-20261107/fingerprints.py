"""Read-only runtime/source binding for the existing A01 fixture and review CLI."""
import ast
import hashlib
from pathlib import Path
import runpy

# Reuse the prior product/binary binder, extending only this run's test surface.
runpy.run_path('docs/evidence/m5/inspection-constraints-20261106/fingerprints.py')
runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
seen = set()
pending = [Path('test/e2e/benchmark_cli.py'), Path('test/e2e/m3_fixed_inputs_test.py')]
while pending:
    path = pending.pop()
    if path in seen:
        continue
    seen.add(path)
    for node in ast.walk(ast.parse(path.read_text())):
        modules = ([node.module] if isinstance(node, ast.ImportFrom) else
                   [n.name for n in node.names] if isinstance(node, ast.Import) else [])
        for module in modules:
            local = path.parent / ((module or '').split('.')[0] + '.py')
            if local.is_file():
                pending.append(local)
seen.update(Path('test/fixtures/agent-p0').glob('*.json'))
for path in sorted(seen):
    raw = path.read_bytes()
    assert raw == (runtime / path).read_bytes(), path
    print(hashlib.sha256(raw).hexdigest(), path)
print(f'PASS: {len(seen)} A01 fixture/CLI/import/JSON bindings checked (may overlap prior binder)')
