"""Temporary diagnostic wrapper; never prints request bodies, results, or credentials."""
import json
import os
from pathlib import Path
import resource
import runpy
import sys
import time


def mark(output, event, **fields):
    with open(output, "a") as log:
        log.write(json.dumps({"pid": os.getpid(), "event": event, "epoch": time.time(), **fields}, sort_keys=True)+"\n")


def main():
    mode = sys.argv[1]
    if mode == "child":
        source, model, stage, output = sys.argv[2:]
        started = time.monotonic()
        mark(output, "child_wrapper_enter", stage=stage)
        seen = set()

        def trace(frame, event, arg):
            if frame.f_code.co_filename != source or frame.f_code.co_name not in ("_main", "_tokenize", "_infer", "_verify_model", "_verify_dependencies"):
                return None
            key = (frame.f_code.co_name, frame.f_lineno)
            if event == "line" and key not in seen:
                seen.add(key)
                usage = resource.getrusage(resource.RUSAGE_SELF)
                io = dict(line.split(": ") for line in Path("/proc/self/io").read_text().splitlines())
                record = {"pid": os.getpid(), "stage": stage, "function": key[0], "line": key[1],
                          "elapsed": round(time.monotonic()-started, 3), "epoch": time.time(),
                          "cpu": round(usage.ru_utime+usage.ru_stime, 3), "maxrss_kib": usage.ru_maxrss,
                          "major_faults": usage.ru_majflt, "read_bytes": int(io["read_bytes"])}
                with open(output, "a") as log:
                    log.write(json.dumps(record, sort_keys=True)+"\n")
            return trace

        sys.argv = [source, model, stage]
        sys.settrace(trace)
        try:
            runpy.run_path(source, run_name="__main__")
        finally:
            sys.settrace(None)
            mark(output, "child_wrapper_exit", stage=stage)
    elif mode == "service":
        output, model, executable, lock = sys.argv[2:]
        from smartdocs import embedding_service, embeddings
        original = embeddings._run_child

        def traced_child(command, payload, timeout, **kwargs):
            stage = command[5]
            command = command[:3]+[str(Path(__file__).resolve()), "child", command[3], command[4], stage, output]
            mark(output, "parent_spawn", stage=stage, timeout=timeout)
            try:
                result = original(command, payload, timeout, **kwargs)
                mark(output, "parent_result", stage=stage)
                return result
            except embeddings.EmbeddingError as error:
                mark(output, "parent_error", stage=stage, code=error.code)
                raise

        embeddings._run_child = traced_child
        sys.argv = [sys.argv[0], model, executable, lock]
        mark(output, "service_enter")
        result = embedding_service.main()
        mark(output, "service_exit")
        return result
    elif mode == "client":
        source, output = sys.argv[2:]
        sys.path.insert(0, str(Path(source).resolve().parent))
        from smartdocs import vector_worker
        original = vector_worker.request_encoding

        def timed_request(path, chunks):
            mark(output, "client_request")
            try:
                result = original(path, chunks)
                mark(output, "client_result")
                return result
            except Exception as error:
                mark(output, "client_error", code=getattr(error, "code", type(error).__name__))
                raise

        vector_worker.request_encoding = timed_request
        sys.argv = [source]
        runpy.run_path(source, run_name="__main__")
    else:
        raise ValueError("unknown diagnostic mode")


if __name__ == "__main__":
    raise SystemExit(main())
