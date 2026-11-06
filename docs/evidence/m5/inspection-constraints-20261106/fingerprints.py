"""Bind the selected offline tests and product to the bounded test runtime."""
import ast
import hashlib
from pathlib import Path

runtime = Path('/tmp/smartdocs-safe.h4SzvX/repo')
paths = {p for folder in ('code', 'db', 'third_party', 'resources')
         for p in Path(folder).rglob('*') if p.is_file()}
paths.update(Path('python/smartdocs').glob('*.py'))
paths.update(Path('test/mcp').glob('*.py'))
paths.add(Path('test/e2e/agent_ui_test.mjs'))
pending = [Path('test/e2e') / name for name in (
    'm3_inspection_constraints_test.py', 'm3_task_list_test.py',
    'm3_handover_report_test.py', 'm3_write_tool_audit_test.py',
    'm3_report_mcp_test.py', 'm3_inspection_mcp_test.py',
    'm3_inspection_browser_test.py')]
while pending:
    path = pending.pop()
    if path in paths:
        continue
    paths.add(path)
    for node in ast.walk(ast.parse(path.read_text())):
        modules = ([node.module] if isinstance(node, ast.ImportFrom) else
                   [n.name for n in node.names] if isinstance(node, ast.Import) else [])
        for module in modules:
            local = path.parent / ((module or '').split('.')[0] + '.py')
            if local.is_file():
                pending.append(local)
paths.update(Path('scripts').glob('*.sh'))
paths.update(Path('test/e2e').glob('*.sh'))
paths.add(Path('Makefile'))
for path in sorted(paths):
    raw = path.read_bytes()
    assert raw == (runtime / path).read_bytes(), path
    print(hashlib.sha256(raw).hexdigest(), path)
for name in ('server', 'smartdocs-admin'):
    path = runtime / 'bin' / name
    print(hashlib.sha256(path.read_bytes()).hexdigest(), path)
print(f'PASS: {len(paths)} source/config/test files match runtime; two binaries bound')
