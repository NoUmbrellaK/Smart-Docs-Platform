"""Throwaway sequential tokenizer/ORT experiment, not a production worker."""
from hashlib import sha256
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import socket
import sys
import time

if os.geteuid() == 0 or len(sys.argv) != 6 or sys.argv[4] not in ('baseline', 'serial', 'tokens'):
    raise SystemExit('invalid non-root experimental invocation')
code_dir, model_dir, lock, mode, expected = sys.argv[1:]
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
    print(json.dumps(dict(event=event, mode=mode, seconds=round(time.monotonic()-started, 3),
        memory_kib_except_threads=status, **extra)), flush=True)


texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
         '部署流程与备份恢复验证。'*80, 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复',
         '<s> [MASK] </s>', '如何恢复备份？', 'x'*1200]
chunks = [{'chunk_id': str(i)*64, 'text': text} for i, text in enumerate(texts)]
embeddings.validate_chunks(chunks)
if any(importlib.metadata.version(n) != v for n, v in embeddings.VERSIONS.items()):
    raise SystemExit('dependency mismatch')

if mode == 'tokens':
    from fastembed.common.preprocessor_utils import load_tokenizer
    tokenizer, _ = load_tokenizer(Path(model_dir))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    prepared = []
    for chunk in chunks:
        windows = []
        for start, end in embeddings.window_ranges(chunk['text'], lambda t: len(tokenizer.encode(t).ids)):
            encoded = tokenizer.encode_batch([chunk['text'][start:end]])[0]
            assert 1 <= len(encoded.ids) <= 128
            windows.append(dict(start=start, end=end, ids=encoded.ids, mask=encoded.attention_mask))
        prepared.append(windows)
    assert sum(map(len, prepared)) <= 128
    print(json.dumps(dict(chunks=prepared,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)), flush=True)
    raise SystemExit(0)

snapshot('start')
with worker_lock(lock):
    if mode == 'baseline':
        result = embeddings._encode(chunks, model_dir)
    else:
        embeddings._verify_model(model_dir)
        prepared = embeddings._run_child([sys.executable, '-I', '-B', __file__,
            code_dir, model_dir, lock, 'tokens', '-'], b'', 60)
        snapshot('tokenizer_exited', tokenizer_peak_rss_kib=prepared['peak_rss_kib'])
        import numpy as np
        import onnxruntime as ort
        from fastembed.common.utils import mean_pooling
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        session = ort.InferenceSession(str(Path(model_dir)/'model_optimized.onnx'),
            providers=['CPUExecutionProvider'], sess_options=options)
        assert session.get_providers() == ['CPUExecutionProvider']
        names = {node.name for node in session.get_inputs()}
        assert names <= {'input_ids', 'attention_mask', 'token_type_ids'}
        snapshot('session_loaded')
        result = dict(embedding_revision=embeddings.EMBEDDING_REVISION, chunks=[])
        for chunk, windows in zip(chunks, prepared['chunks']):
            encoded = dict(chunk_id=chunk['chunk_id'],
                text_sha256=sha256(chunk['text'].encode()).hexdigest(), windows=[])
            for window in windows:
                ids = np.array([window['ids']], dtype=np.int64)
                mask = np.array([window['mask']], dtype=np.int64)
                feed = dict(input_ids=ids, attention_mask=mask, token_type_ids=np.zeros_like(ids))
                output = session.run(None, {k: v for k, v in feed.items() if k in names})[0]
                vector = [float(v) for v in mean_pooling(output, mask).astype(output.dtype, copy=False)[0]]
                norm = math.sqrt(math.fsum(v*v for v in vector))
                encoded['windows'].append(dict(char_start=window['start'], char_end=window['end'],
                    vector=[v/norm for v in vector]))
            result['chunks'].append(encoded)
    embeddings.validate_result(result, chunks)
    digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
    snapshot('encoded', output_sha256=digest, chunks=len(chunks),
        windows=sum(len(c['windows']) for c in result['chunks']))
    if expected != '-':
        assert digest == expected, 'complete output differs from baseline'
snapshot('complete', matched_control=expected != '-')
