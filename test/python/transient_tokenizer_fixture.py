"""Real child-process transport fixture; no model quality claims."""
import fcntl
import json
import os
from pathlib import Path
import signal
import sys

mode, marker, *fds = sys.argv[1:]
Path(marker).write_text(json.dumps({'pid': os.getpid(), 'group': os.getpgrp()}))
assert 'SMARTDOCS_TEST_SECRET' not in os.environ
for fd in map(int, fds):
    assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE
    assert os.pread(fd, 3, 0) == b'abc'
assert json.load(sys.stdin) == [{'chunk_id': 'a'*64, 'text': 'abc'}]
if mode == 'stop':
    os.kill(os.getpid(), signal.SIGSTOP)
elif mode == 'crash':
    sys.exit(7)
elif mode == 'oversize':
    sys.stdout.write('x'*262145)
elif mode == 'bad':
    sys.stdout.write('{}')
elif mode == 'invalid':
    sys.stdout.write('{"error":"embedding_invalid_input"}')
else:
    from smartdocs.embeddings import EMBEDDING_REVISION
    print(json.dumps({'embedding_revision': EMBEDDING_REVISION, 'chunks': [{
        'chunk_id': 'a'*64, 'text_sha256': 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
        'windows': [{'char_start': 0, 'char_end': 3, 'ids': [1, 2], 'mask': [1, 1]}]}]}))
