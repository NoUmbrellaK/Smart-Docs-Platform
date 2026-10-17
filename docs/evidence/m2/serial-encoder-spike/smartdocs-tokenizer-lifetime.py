"""Throwaway tokenizer lifetime probe; not production code."""
import ctypes
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
import time

if os.geteuid() == 0 or len(sys.argv) != 4:
    raise SystemExit('requires non-root and code/model/lock paths')
code_dir, model_dir, lock = sys.argv[1:]
os.environ.clear()
os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
    HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TOKENIZERS_PARALLELISM='false',
    OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        connection = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        pass
    else:
        connection.close()
        raise SystemExit('IP socket isolation required')
sys.path.insert(0, code_dir)
from smartdocs import embeddings
from smartdocs.worker import worker_lock
embeddings._set_limits()
started = time.monotonic()


def snapshot(event, **extra):
    status = {}
    for line in Path('/proc/self/status').read_text().splitlines():
        key, _, value = line.partition(':')
        if key in ('VmRSS', 'VmHWM', 'RssAnon', 'RssFile', 'Threads'):
            status[key] = int(value.split()[0])
    print(json.dumps(dict(event=event, seconds=round(time.monotonic()-started, 3),
        memory_kib_except_threads=status, **extra)), flush=True)


libc = ctypes.CDLL(None)
trim = libc.malloc_trim
trim.argtypes, trim.restype = [ctypes.c_size_t], ctypes.c_int
texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
         '部署流程与备份恢复验证。'*80, '', 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复', '<s> [MASK] </s>']
with worker_lock(lock):
    embeddings._verify_model(model_dir)
    from tokenizers import Tokenizer
    from fastembed.common.preprocessor_utils import load_tokenizer
    snapshot('imported')
    tokenizer = Tokenizer.from_file(str(Path(model_dir)/'tokenizer.json'))
    snapshot('raw_loaded')
    tokenizer.no_truncation()
    tokenizer.no_padding()
    reference = [tokenizer.encode(t).ids for t in texts]
    gc.collect()
    snapshot('raw_retained_trimmed', trim_result=trim(0))
    del tokenizer
    gc.collect()
    snapshot('raw_deleted')
    snapshot('raw_deleted_trimmed', trim_result=trim(0))
    tokenizer, special_tokens = load_tokenizer(Path(model_dir))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    actual = [tokenizer.encode(t).ids for t in texts]
    assert actual == reference
    snapshot('fastembed_loaded_equal', cases=len(texts),
        token_counts=list(map(len, actual)),
        ids_sha256=sha256(json.dumps(actual).encode()).hexdigest())
    gc.collect()
    snapshot('fastembed_retained_trimmed', trim_result=trim(0))
    del tokenizer
    gc.collect()
    snapshot('fastembed_deleted_trimmed', trim_result=trim(0))
snapshot('complete')
