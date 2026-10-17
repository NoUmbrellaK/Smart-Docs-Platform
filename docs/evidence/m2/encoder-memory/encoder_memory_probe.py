"""Throwaway memory diagnosis, not a service or a production encoder change."""
import ctypes
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time


def snapshot(event, **extra):
    status = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('VmRSS', 'VmHWM', 'RssAnon', 'RssFile', 'Threads'):
            status[key] = int(value.split()[0])
    print(json.dumps({'event': event, 'seconds': round(time.monotonic()-started, 3),
                      'memory_kib_except_threads': status, **extra}), flush=True)


if os.geteuid() == 0 or len(sys.argv) != 5 or sys.argv[4] not in ('default', 'no-arena'):
    raise SystemExit('invalid experimental invocation')
code_dir, model_dir, lock, mode = sys.argv[1:]
os.environ.clear()
os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
    HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TOKENIZERS_PARALLELISM='false',
    OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
for family in (socket.AF_INET, socket.AF_INET6):
    try: probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError: pass
    else:
        probe.close()
        raise SystemExit('IP socket isolation required')
sys.path.insert(0, code_dir)
from smartdocs import embeddings
from smartdocs.worker import worker_lock
embeddings._set_limits()
started = time.monotonic()
snapshot('start', mode=mode)
with worker_lock(lock):
    import fastembed
    constructor = fastembed.TextEmbedding
    model = None
    signature = None
    constructions = 0

    def retained_model(*args, **kwargs):
        global model, signature, constructions
        if mode == 'no-arena': kwargs['enable_cpu_mem_arena'] = False
        current = (args, {k: v for k, v in kwargs.items() if k != 'cache_dir'})
        if model is None:
            model = constructor(*args, **kwargs)
            signature = current
            constructions += 1
        if current != signature:
            raise RuntimeError('experimental constructor changed')
        return model

    fastembed.TextEmbedding = retained_model
    chunks = [
        {'chunk_id': '0'*64, 'text': 'Database disaster recovery: restore the latest backup to recover lost data.'},
        {'chunk_id': '1'*64, 'text': '部署流程与备份恢复验证。'*80},
    ]
    embeddings.validate_chunks(chunks)
    reference = embeddings._encode(chunks, model_dir)
    embeddings.validate_result(reference, chunks)
    fingerprint = sha256(json.dumps(reference, sort_keys=True, allow_nan=False).encode()).hexdigest()
    snapshot('encoded', windows=sum(len(c['windows']) for c in reference['chunks']),
             output_sha256=fingerprint,
             cpu_mem_arena=model.model.model.get_session_options().enable_cpu_mem_arena)
    collected = gc.collect()
    snapshot('after_gc', collected=collected)
    libc = ctypes.CDLL(None)
    trim = libc.malloc_trim
    trim.argtypes, trim.restype = [ctypes.c_size_t], ctypes.c_int
    released = trim(0)
    snapshot('after_trim', malloc_trim_result=released)
    repeat_started = time.monotonic()
    repeated = embeddings._encode(chunks, model_dir)
    embeddings.validate_result(repeated, chunks)
    if repeated != reference or constructions != 1:
        raise RuntimeError('experimental repeat changed output or rebuilt model')
    snapshot('repeat_equal', request_seconds=round(time.monotonic()-repeat_started, 3),
             constructions=constructions, output_sha256=fingerprint)
    snapshot('after_second_trim', malloc_trim_result=trim(0))
snapshot('complete', cpu_seconds=round(resource.getrusage(resource.RUSAGE_SELF).ru_utime+
                                      resource.getrusage(resource.RUSAGE_SELF).ru_stime, 3))
