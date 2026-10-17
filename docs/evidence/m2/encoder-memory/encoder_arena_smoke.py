"""Bounded real-child regression using artificial inputs and a measured control digest."""
from hashlib import sha256
import json
import resource
import sys
import time

sys.path.insert(0, '/tmp/smartdocs-safe.h4SzvX/repo/python')
from smartdocs.embeddings import encode_chunks

chunks = [
    {'chunk_id': '0'*64, 'text': 'Database disaster recovery: restore the latest backup to recover lost data.'},
    {'chunk_id': '1'*64, 'text': '部署流程与备份恢复验证。'*80},
]
started = time.monotonic()
result = encode_chunks(chunks, model_dir='/tmp/smartdocs-safe.h4SzvX/vector-probe/model',
    python_executable='/tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python',
    lock_path='/tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock')
digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
if digest != 'd9bc3c19fca7336bd245b9d7ffa742f1eb36647589cabdb75f7086f31f8e0226':
    raise RuntimeError('real child differs from measured default-arena control')
print(json.dumps({'event': 'real_child_matches_control', 'seconds': round(time.monotonic()-started, 3),
    'windows': sum(len(c['windows']) for c in result['chunks']), 'output_sha256': digest,
    'child_peak_rss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}), flush=True)
