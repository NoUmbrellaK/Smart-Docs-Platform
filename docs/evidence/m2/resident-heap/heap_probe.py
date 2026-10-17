"""Disposable Linux/glibc attribution; not a production memory policy."""
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

from smartdocs.resident_embeddings import ResidentEncoder
from smartdocs import embeddings as base
from smartdocs.worker import worker_lock


class Mallinfo2(ctypes.Structure):
    # ABI from this host's /usr/include/malloc.h: ten size_t fields.
    _fields_ = [(name, ctypes.c_size_t) for name in (
        'arena', 'ordblks', 'smblks', 'hblks', 'hblkhd', 'usmblks',
        'fsmblks', 'uordblks', 'fordblks', 'keepcost')]


libc = ctypes.CDLL('libc.so.6')
libc.mallinfo2.argtypes = []
libc.mallinfo2.restype = Mallinfo2
libc.malloc_trim.argtypes = [ctypes.c_size_t]
libc.malloc_trim.restype = ctypes.c_int
started = time.monotonic()


def snapshot(stage, **extra):
    memory = {}
    for line in Path('/proc/self/smaps_rollup').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in {'Rss', 'Pss', 'Anonymous', 'Private_Dirty', 'Shared_Clean'}:
            memory[key + '_kib'] = int(value.split()[0])
    info = libc.mallinfo2()
    print(json.dumps({'stage': stage, 'elapsed_s': round(time.monotonic()-started, 3),
        **memory, 'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'allocator_bytes': {key: getattr(info, key) for key in
            ('arena', 'hblkhd', 'uordblks', 'fordblks', 'keepcost')}, **extra}), flush=True)


def main():
    assert os.geteuid() != 0
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            probe = socket.socket(family)
        except OSError:
            continue
        probe.close()
        raise AssertionError('IP sockets must be blocked')
    for kind, bound in ((resource.RLIMIT_AS, 2*1024**3), (resource.RLIMIT_CPU, 60),
                        (resource.RLIMIT_FSIZE, 256*1024**2), (resource.RLIMIT_CORE, 0)):
        soft, hard = resource.getrlimit(kind)
        limit = min(v for v in (soft, hard, bound) if v != resource.RLIM_INFINITY)
        resource.setrlimit(kind, (limit, limit))
    texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
             '部署流程与备份恢复验证。'*80, 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复',
             '<s> [MASK] </s>', '如何恢复备份？', 'x'*1200]
    chunks = [{'chunk_id': str(i)*64, 'text': text} for i, text in enumerate(texts)]
    with worker_lock(sys.argv[2]):
        snapshot('before_constructor')
        engine = ResidentEncoder(sys.argv[1])
        base._set_limits()
        snapshot('after_constructor')
        snapshot('after_gc', collected=gc.collect())
        snapshot('after_trim', trim_return=libc.malloc_trim(0))
        for i in range(5):
            begin = time.monotonic()
            result = engine.encode(chunks)
            digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
            assert digest == '6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8', digest
            snapshot('request_' + str(i+1), request_s=round(time.monotonic()-begin, 3),
                     compatibility_sha256=digest)
        snapshot('after_requests_trim', trim_return=libc.malloc_trim(0))
        # Destructive only to this disposable instance; no encoding after deletion.
        del engine._tokenizer
        gc.collect()
        snapshot('without_tokenizer_trim', trim_return=libc.malloc_trim(0))
        del engine
        gc.collect()
        snapshot('without_model_trim', trim_return=libc.malloc_trim(0))
    print(json.dumps({'passed': True, 'requests': 5, 'remote_calls': 0}), flush=True)


if __name__ == '__main__':
    main()
