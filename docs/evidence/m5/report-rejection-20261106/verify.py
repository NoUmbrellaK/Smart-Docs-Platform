"""Check observed offline evidence; does not infer H05 correctness or semantic acceptance."""
import pathlib
import re
import subprocess
import sys

here=pathlib.Path('docs/evidence/m5/report-rejection-20261106')
assert 'FAILED (failures=14)' in (here/'red.txt').read_text()
assert 'None !=' in (here/'red.txt').read_text()
build=(here/'build.txt').read_text()
assert 'Deactivated successfully.' in build and 'warning:' not in build and 'error:' not in build
count=0
for phase,expected in (('green',15),('writeaudit',12),('readaudit',4),('mcp',4)):
    log=(here/(phase+'.txt')).read_text()
    assert re.search(rf'^Ran {expected} tests in [0-9.]+s$',log,re.M),phase
    assert '\nOK\n' in log and 'Deactivated successfully.' in log,phase
    assert 'FAILED' not in log and 'ERROR:' not in log,phase
    count+=expected
assert count==35
initial=(here/'preflight.txt').read_text().splitlines()
final=(here/'postflight.txt').read_text().splitlines()
assert len(initial)==len(final)
changes=[(a,b) for a,b in zip(initial,final) if a!=b]
assert len(changes)==1 and all(line.endswith(' test/e2e/m3_report_mcp_test.py') for line in changes[0])
assert (here/'mcp-preflight.txt').read_bytes()==(here/'postflight.txt').read_bytes()
failed=(here/'initial-mcp.txt').read_text()
assert 'FAILED (failures=3)' in failed and 'AssertionError: 2 != 3' in failed
assert subprocess.check_output([sys.executable,'-B',str(here/'fingerprints.py')])==(here/'postflight.txt').read_bytes()
for name in ('fixed-task-results.json','supplemental-task-results.json','h05-diagnostic-20261106/result.json'):
    path=pathlib.Path('docs/evidence/m5')/name
    assert path.read_bytes()==subprocess.check_output(['git','show','263e7b7:'+str(path)])
status=(here/'final-status.txt').read_text().splitlines()
assert all(s in status for s in ('TasksCurrent=0','high 82471','max 0','oom 0','oom_kill 0','active'))
for p in here.iterdir():
    if p.is_file(): assert not re.search(r'sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----',p.read_text()),p
print('PASS: RED14 missing-category failures preserved; incremental server/admin build completed')
print('PASS: report15 + writeaudit12 + readaudit4 + MCP4 = 35/35 offline method executions; 31 distinct methods, read audit4 repeated via inheritance')
print('PASS: 151 source/config/test files match runtime; only MCP fixture changed between initial and final fingerprints; binaries unchanged')
print('PASS: initial MCP 3/4 failures retained; corrected fixture selects the search tool reply before coverage notice')
print('PASS: business ledgers and H05 historical failure unchanged; no human verdict or model-success claim')
print('PASS: test slice empty, original MySQL active, high/max/OOM counters unchanged')
