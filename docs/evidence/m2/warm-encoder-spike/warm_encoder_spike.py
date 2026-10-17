"""Throwaway feasibility probe, NOT a production service. Trusted local CLI only."""
import json
import os
from pathlib import Path
import resource
import signal
import socket
import sys
import tempfile
import time


def fork_encode(chunks, model_dir, listener, connection, embeddings):
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        child = os.fork()
        if child == 0:
            os.setsid()
            listener.close()
            connection.close()
            try:
                embeddings._set_limits()
                result = embeddings._encode(chunks, model_dir)
                embeddings.validate_result(result, chunks)
                body = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
                if len(body) > embeddings.MAX_OUTPUT_BYTES:
                    raise embeddings.EmbeddingError("embedding_result_too_large")
            except embeddings.EmbeddingError as error:
                body = json.dumps({"error": error.code}).encode()
            except Exception:
                body = b'{"error":"embedding_worker_failed"}'
            try:
                output.write(body)
                output.flush()
            finally:
                os._exit(0)
        try:
            while True:
                pid, status = os.waitpid(child, os.WNOHANG)
                if pid:
                    if status != 0:
                        raise embeddings.EmbeddingError("embedding_worker_failed")
                    break
                if time.monotonic()-started >= 60:
                    raise embeddings.EmbeddingError("embedding_timeout")
                time.sleep(.05)
        finally:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                try: os.kill(child, signal.SIGKILL)
                except ProcessLookupError: pass
            try: os.waitpid(child, 0)
            except ChildProcessError: pass
        output.seek(0)
        body = output.read(embeddings.MAX_OUTPUT_BYTES+1)
        if len(body) > embeddings.MAX_OUTPUT_BYTES:
            raise embeddings.EmbeddingError("embedding_result_too_large")
        result = json.loads(body)
        if "error" in result:
            raise embeddings.EmbeddingError(result["error"])
        embeddings.validate_result(result, chunks)
        print(json.dumps({"event": "request_completed", "seconds": round(time.monotonic()-started, 3),
                          "parent_rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)
        return body


def main():
    code_dir, model_dir, endpoint, lock = sys.argv[1:]
    assert os.geteuid() != 0
    notification = os.environ["NOTIFY_SOCKET"]
    os.environ.clear()
    os.environ.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1", TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    for family in (socket.AF_INET, socket.AF_INET6):
        try: probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError: pass
        else:
            probe.close()
            raise RuntimeError("IP socket isolation required")
    sys.path.insert(0, code_dir)
    from smartdocs import embeddings
    from smartdocs.worker import worker_lock
    embeddings._set_limits()
    assert not os.path.lexists(endpoint)
    info = Path(endpoint).parent.stat()
    assert info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0
    with worker_lock(lock):
        started = time.monotonic()
        import fastembed
        constructor = fastembed.TextEmbedding
        cached = None
        signature = None

        def reused_constructor(*args, **kwargs):
            nonlocal cached, signature
            current = (args, {k: v for k, v in kwargs.items() if k != "cache_dir"})
            if cached is None:
                cached = constructor(*args, **kwargs)
                signature = current
            assert current == signature
            return cached

        fastembed.TextEmbedding = reused_constructor
        warmup = [{"chunk_id": "0"*64, "text": "Artificial initialization probe."}]
        embeddings.validate_result(embeddings._encode(warmup, model_dir), warmup)
        print(json.dumps({"event": "initialized", "seconds": round(time.monotonic()-started, 3),
                          "rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}), flush=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(endpoint)
            os.chmod(endpoint, 0o600)
            listener.listen(1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
                notify.connect(notification.replace("@", "\0", 1) if notification.startswith("@") else notification)
                notify.sendall(b"READY=1")
            # Deliberately bounded experimental lifetime; not a production queue design.
            for _ in range(32):
                connection, _ = listener.accept()
                with connection:
                    try:
                        deadline = time.monotonic()+5
                        raw = bytearray()
                        while True:
                            remaining = deadline-time.monotonic()
                            if remaining <= 0: raise TimeoutError()
                            connection.settimeout(remaining)
                            part = connection.recv(min(65536, embeddings.MAX_INPUT_BYTES+1-len(raw)))
                            if not part: break
                            raw.extend(part)
                            if len(raw) > embeddings.MAX_INPUT_BYTES:
                                raise embeddings.EmbeddingError("embedding_invalid_input")
                        chunks = json.loads(raw)
                        embeddings.validate_chunks(chunks)
                        body = fork_encode(chunks, model_dir, listener, connection, embeddings)
                    except embeddings.EmbeddingError as error:
                        code = error.code if error.code in embeddings.ENCODING_ERRORS else "embedding_worker_failed"
                        print(json.dumps({"event": "request_failed", "code": code}), flush=True)
                        body = json.dumps({"error": code}).encode()
                    except (ValueError, UnicodeError, TimeoutError):
                        body = b'{"error":"embedding_invalid_input"}'
                    connection.settimeout(5)
                    try: connection.sendall(body)
                    except OSError: pass


if __name__ == "__main__":
    main()
