"""Disposable model-resident/tokenizer-transient feasibility check, not a service."""
from contextlib import ExitStack
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import tempfile
import time

from smartdocs import embeddings as base
from smartdocs.resident_embeddings import verified_snapshot
from smartdocs.worker import worker_lock

ENV = {'PYTHONPATH': str(Path(base.__file__).resolve().parents[1]),
       'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '1',
       'OPENBLAS_NUM_THREADS': '1', 'HF_HUB_OFFLINE': '1',
       'HF_HUB_DISABLE_TELEMETRY': '1', 'HF_HUB_DISABLE_IMPLICIT_TOKEN': '1'}


def limits():
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


def tokenize():
    base._set_limits()
    tokenizer_fd, tokens_fd = map(int, sys.argv[2:4])
    required = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    for fd in (tokenizer_fd, tokens_fd):
        assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & required == required
    raw = sys.stdin.buffer.read(base.MAX_INPUT_BYTES+1)
    assert len(raw) <= base.MAX_INPUT_BYTES
    chunks = json.loads(raw)
    base.validate_chunks(chunks)
    from tokenizers import AddedToken, Tokenizer
    engine = Tokenizer.from_file(f'/proc/self/fd/{tokenizer_fd}')
    with open(f'/proc/self/fd/{tokens_fd}') as source:
        for token in json.load(source).values():
            engine.add_special_tokens([AddedToken(**token)])
    engine.no_truncation()
    engine.no_padding()
    result = json.dumps(base._prepare_chunks(chunks, engine), allow_nan=False).encode()
    assert len(result) <= base.MAX_STAGE_BYTES
    memory('tokenizer_live', tokenizer_pid=os.getpid())
    sys.stdout.buffer.write(result)


def memory(stage, **extra):
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    group = Path('/sys/fs/cgroup') / Path('/proc/self/cgroup').read_text().strip().split('::')[1].lstrip('/')
    print(json.dumps({'stage': stage, 'rss_kib': int(status['VmRSS'].split()[0]),
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'child_peak_rss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        'group_memory_bytes': int((group/'memory.current').read_text()), **extra}),
        file=sys.stderr if sys.argv[1] == '--tokenize' else sys.stdout, flush=True)


def prepared(chunks, fds):
    payload = json.dumps(chunks, ensure_ascii=False).encode()
    assert len(payload) <= base.MAX_INPUT_BYTES
    begin = time.monotonic()
    with tempfile.TemporaryFile() as output:
        # Same process group: the owning systemd unit must kill all descendants.
        child = subprocess.Popen([sys.executable, '-B', __file__, '--tokenize', *map(str, fds)],
            stdin=subprocess.PIPE, stdout=output, stderr=None,
            env=ENV, pass_fds=fds, start_new_session=False)
        try:
            child.communicate(payload, timeout=60)
            assert child.returncode == 0, f'tokenizer failed: {child.returncode}'
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        assert not Path(f'/proc/{child.pid}').exists(), 'tokenizer survived wait'
        output.seek(0)
        raw = output.read(base.MAX_STAGE_BYTES+1)
    assert len(raw) <= base.MAX_STAGE_BYTES
    result = json.loads(raw)
    base.validate_prepared(result, chunks)
    return result, child.pid, time.monotonic()-begin


def main():
    texts = ['Database disaster recovery: restore the latest backup to recover lost data.',
             '部署流程与备份恢复验证。'*80, 'ＡＢＣ café e\u0301 👩‍💻\n备份\t恢复',
             '<s> [MASK] </s>', '如何恢复备份？', 'x'*1200]
    chunks = [{'chunk_id': str(i)*64, 'text': text} for i, text in enumerate(texts)]
    base.validate_chunks(chunks)
    with worker_lock(sys.argv[2]), tempfile.TemporaryDirectory(prefix='transient-tokenizer-') as directory:
        local = Path(directory)
        for name in base.MODEL_HASHES:
            os.link(Path(sys.argv[1])/name, local/name)
        begin = time.monotonic()
        base._verify_dependencies()
        with ExitStack() as stack:
            snapshots = {name: stack.enter_context(verified_snapshot(str(local), name))
                for name in sorted(base.MODEL_HASHES, key=lambda name: name.endswith('.onnx'))}
            weights = snapshots['model_optimized.onnx']
            model = base._new_session(f'/proc/self/fd/{weights.fileno()}')
            model.disable_fallback()
            weights.close()
            fds = tuple(snapshots[name].fileno() for name in ('tokenizer.json', 'special_tokens_map.json'))
            for name in ('config.json', 'tokenizer_config.json'):
                snapshots[name].close()
            # Only remove fixture hard links; the original model is never altered.
            for name in base.MODEL_HASHES:
                (local/name).unlink()
            base._set_limits()
            memory('ready', startup_s=round(time.monotonic()-begin, 3),
                   retained_snapshot_bytes=sum(os.fstat(fd).st_size for fd in fds))
            for i in range(5):
                begin = time.monotonic()
                tokens, pid, tokenize_s = prepared(chunks, fds)
                memory('tokenizer_exited_' + str(i+1), tokenizer_pid=pid)
                result = base._infer_windows(chunks, tokens, model)
                elapsed = time.monotonic()-begin
                digest = sha256(json.dumps(result, sort_keys=True, allow_nan=False).encode()).hexdigest()
                assert digest == '6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8', digest
                assert elapsed < 60, 'request exceeded original total budget'
                memory('request_' + str(i+1), tokenizer_pid=pid, request_s=round(elapsed, 3),
                       tokenize_s=round(tokenize_s, 3), compatibility_sha256=digest)
            assert not Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children').read_text().strip()
        for fd in fds:
            try:
                os.fstat(fd)
            except OSError:
                continue
            raise AssertionError('snapshot fd leaked')
    print(json.dumps({'passed': True, 'requests': 5, 'production_entry_unchanged': True}), flush=True)


if __name__ == '__main__':
    limits()
    if sys.argv[1] == '--tokenize':
        tokenize()
    else:
        main()
