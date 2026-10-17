"""Test-only ordering barrier; preserves all original browser test assertions."""
import os
from pathlib import Path
import runpy
import stat
import sys
import time

sys.path.insert(0, "/tmp/smartdocs-safe.h4SzvX/repo/test/e2e")
from m1_browser_test import Browser

original_start = Browser.start


def start_then_wait(self):
    original_start(self)
    print("EXPERIMENT browser ready; waiting for separately capped model initialization", flush=True)
    endpoint = Path(os.environ["SMARTDOCS_ENCODER_SOCKET"])
    deadline = time.monotonic()+240
    while not endpoint.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("experimental initialization barrier timed out")
        time.sleep(.2)
    info = endpoint.stat()
    if not stat.S_ISSOCK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
        raise RuntimeError("invalid experimental encoder socket")
    print("EXPERIMENT encoder ready; continuing unchanged test", flush=True)


Browser.start = start_then_wait
runpy.run_path("/tmp/smartdocs-safe.h4SzvX/repo/test/e2e/hybrid_browser_test.py", run_name="__main__")
