"""Throwaway native-option diagnosis; never imported by the application."""
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time

if os.geteuid() == 0 or len(sys.argv) != 5 or sys.argv[4] not in ('0', '1'):
    raise SystemExit('invalid experimental invocation')
code_dir, model_dir, lock, disable = sys.argv[1:]
os.environ.clear()
os.environ.update(HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
    HF_HUB_DISABLE_IMPLICIT_TOKEN='1', TOKENIZERS_PARALLELISM='false',
    OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
for family in (socket.AF_INET, socket.AF_INET6):
    try:
        probe = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        pass
    else:
        probe.close()
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


snapshot('start', disable_prepacking=disable)
with worker_lock(lock):
    import fastembed
    import onnxruntime as ort
    snapshot('imported')
    options_constructor = ort.SessionOptions

    def session_options():
        options = options_constructor()
        options.add_session_config_entry('session.disable_prepacking', disable)
        return options

    # Experiment-only interception because FastEmbed does not expose this option.
    ort.SessionOptions = session_options
    constructor = fastembed.TextEmbedding
    retained = None
    constructions = 0

    def retain_model(*args, **kwargs):
        global retained, constructions
        if retained is None:
            snapshot('before_model')
            retained = constructor(*args, **kwargs)
            constructions += 1
            actual = retained.model.model.get_session_options()
            assert actual.get_session_config_entry('session.disable_prepacking') == disable
            assert actual.enable_cpu_mem_arena is False
            snapshot('model_ready')
        return retained

    fastembed.TextEmbedding = retain_model
    chunks = [
        {'chunk_id': '0'*64, 'text': 'Database disaster recovery: restore the latest backup to recover lost data.'},
        {'chunk_id': '1'*64, 'text': '部署流程与备份恢复验证。'*80},
    ]
    reference = None
    for iteration in range(2):
        result = embeddings._encode(chunks, model_dir)
        embeddings.validate_result(result, chunks)
        fingerprint = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
        if reference is not None:
            assert result == reference
        reference = result
        snapshot('encoded', iteration=iteration, output_sha256=fingerprint,
            windows=sum(len(c['windows']) for c in result['chunks']))
    assert constructions == 1
snapshot('complete', cpu_seconds=round(resource.getrusage(resource.RUSAGE_SELF).ru_utime+
    resource.getrusage(resource.RUSAGE_SELF).ru_stime, 3))
