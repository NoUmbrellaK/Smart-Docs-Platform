"""One socket-activated request on stdin/stdout; deployment MUST prohibit IP sockets."""
from functools import partial
import json
import os
import socket
import sys

from .embeddings import (ENCODING_ERRORS, MAX_INPUT_BYTES, MAX_OUTPUT_BYTES,
                         EmbeddingError, encode_chunks, validate_chunks, validate_result)


def handle_request(source, output, encoder):
    try:
        raw = source.read(MAX_INPUT_BYTES+1)
        if len(raw) > MAX_INPUT_BYTES: raise EmbeddingError("embedding_invalid_input")
        try: chunks = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise EmbeddingError("embedding_invalid_input") from None
        validate_chunks(chunks)
        result = encoder(chunks)
        validate_result(result, chunks)
        body = json.dumps(result, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(body) > MAX_OUTPUT_BYTES: raise EmbeddingError("embedding_result_too_large")
    except EmbeddingError as error:
        body = json.dumps({"error": error.code if error.code in ENCODING_ERRORS else "embedding_worker_failed"}).encode()
    except Exception:
        body = b'{"error":"embedding_worker_failed"}'
    output.write(body)
    output.flush()


def main():
    if os.geteuid() == 0 or len(sys.argv) != 4:
        sys.stdout.buffer.write(b'{"error":"embedding_worker_failed"}')
        return 1
    # Offline library flags are not a sandbox. Refuse deployments allowing IP sockets.
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        else:
            probe.close()
            sys.stdout.buffer.write(b'{"error":"embedding_worker_failed"}')
            return 1
    model, executable, lock = sys.argv[1:]
    handle_request(sys.stdin.buffer, sys.stdout.buffer,
                   partial(encode_chunks, model_dir=model, python_executable=executable, lock_path=lock))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
