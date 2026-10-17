"""One disposable, sandboxed browser; no login, model or database."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

sys.path.insert(0, '/tmp/smartdocs-safe.h4SzvX/repo/test/e2e')
from m1_browser_test import Browser, dependencies

started = time.monotonic()

def snapshot(browser, stage):
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
    processes = []
    for pid in group.joinpath('cgroup.procs').read_text().split():
        root = Path('/proc') / pid
        try:
            args = root.joinpath('cmdline').read_bytes().decode().split('\0')
            kind_match = re.search(r'--type=([a-z-]+)', ' '.join(args))
            kind = kind_match[1] if kind_match else root.joinpath('comm').read_text().strip()
            status = dict(line.split(':', 1) for line in root.joinpath('status').read_text().splitlines() if ':' in line)
            values = dict(line.split(':', 1) for line in root.joinpath('smaps_rollup').read_text().splitlines() if ':' in line)
            processes.append({'pid': int(pid), 'kind': kind, 'rss_kib': int(values['Rss'].split()[0]),
                'pss_kib': int(values['Pss'].split()[0]), 'private_dirty_kib': int(values['Private_Dirty'].split()[0]),
                'seccomp': status['Seccomp'].strip()})
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            processes.append({'pid': int(pid), 'memory_unavailable': True})
    targets = browser.command('POST', '/goog/cdp/execute', {'cmd': 'Target.getTargets', 'params': {}})['targetInfos']
    print(json.dumps({'stage': stage, 'elapsed_s': round(time.monotonic()-started, 3),
        'url': browser.command('GET', '/url'), 'targets': [{'type': t['type'], 'url': t['url']} for t in targets],
        'group_bytes': int(group.joinpath('memory.current').read_text()), 'processes': processes}), flush=True)

binaries, sandbox = dependencies(version_timeout=120)
assert sandbox == 'enabled'
with tempfile.TemporaryDirectory(prefix='smartdocs-browser-memory-') as profile:
    browser = Browser(binaries, sandbox, profile)
    try:
        browser.start()
        snapshot(browser, 'started')
        if len(sys.argv) == 2:
            assert browser.command('GET', '/url') == sys.argv[1], 'unexpected startup page'
        browser.command('POST', '/url', {'url': 'about:blank'})
        snapshot(browser, 'about_blank')
    finally:
        browser.stop()
print(json.dumps({'stage': 'stopped', 'elapsed_s': round(time.monotonic()-started, 3)}), flush=True)
