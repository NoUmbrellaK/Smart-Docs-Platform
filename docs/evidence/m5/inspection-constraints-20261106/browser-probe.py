"""Bounded local Chrome session diagnosis; no application, SQL, model or keys."""
import os
from pathlib import Path
import tempfile

from m1_browser_test import Browser, dependencies

output = Path(os.environ['SMARTDOCS_INSPECTION_BROWSER_OUTPUT'])
binaries, sandbox = dependencies(version_timeout=120)
assert sandbox == 'enabled'
with tempfile.TemporaryDirectory(prefix='smartdocs-inspection-probe-') as profile:
    browser = Browser(binaries, sandbox, profile, driver_log=output / 'chromedriver.log')
    try:
        browser.start()
        print('PASS sandboxed Chrome session created; no application accessed', flush=True)
    finally:
        browser.stop()
