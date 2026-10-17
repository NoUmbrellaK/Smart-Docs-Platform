"""Startup-only diagnostic: no login/site, no credentials, no driver log reads."""
import json
from pathlib import Path
import runpy
import sys

ROOT = Path('/tmp/smartdocs-safe.h4SzvX/repo')
sys.path.insert(0, str(ROOT/'test/e2e'))
import m1_browser_test as browser

original = browser.webdriver_value

def observed(status, payload):
    if status >= 400:
        value = json.loads(payload).get('value', {})
        # This process only calls status/session creation and about:blank.
        print(json.dumps({'webdriver_startup_error': value.get('error'),
                          'message': str(value.get('message', ''))[:2000]}), flush=True)
    return original(status, payload)

browser.webdriver_value = observed
sys.argv = [str(ROOT/'docs/evidence/m2/browser-startup/browser_memory_probe.py'), 'about:blank']
runpy.run_path(sys.argv[0], run_name='__main__')
