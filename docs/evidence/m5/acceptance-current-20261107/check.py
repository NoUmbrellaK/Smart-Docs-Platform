"""In-memory negative controls for the read-only reconciliation; no file edits."""
import contextlib
import io
import json
from pathlib import Path
import runpy
from unittest.mock import patch

here = Path(__file__).resolve().parent
read_text = Path.read_text
for mutation in ('missing_task', 'filled_verdict', 'wrong_citation_count'):
    def altered(path, *args, **kwargs):
        raw = read_text(path, *args, **kwargs)
        if path.as_posix() == 'docs/evidence/m5/fixed-task-results.json':
            value = json.loads(raw)
            if mutation == 'missing_task':
                value['tasks'].pop()
            elif mutation == 'filled_verdict':
                value['tasks'][0]['business_verdict'] = 'pass'
            return json.dumps(value)
        if mutation == 'wrong_citation_count' and path.as_posix() == 'docs/evidence/m5/q01-live-20261105/result.json':
            value = json.loads(raw)
            value['review_summary']['citations_total'] += 1
            return json.dumps(value)
        return raw

    with patch.object(Path, 'read_text', altered), contextlib.redirect_stdout(io.StringIO()):
        try:
            runpy.run_path(str(here / 'reconcile.py'))
        except AssertionError:
            pass
        else:
            raise RuntimeError('accepted corrupted input: ' + mutation)
    print('REJECTED in-memory negative control:', mutation)
