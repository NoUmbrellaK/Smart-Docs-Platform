"""Test-only timing around the unchanged scenario; never print arguments/results."""
from contextlib import redirect_stdout
from functools import wraps
import io
import json
from pathlib import Path
import runpy
import sys
import time


def observed(stage, call, *, slow_only=False):
    @wraps(call)
    def invoke(*args, **kwargs):
        start = time.monotonic()
        outcome = "ok"
        if not slow_only:
            print(json.dumps({"stage": stage, "event": "begin"}), flush=True)
        try:
            return call(*args, **kwargs)
        except Exception as error:
            outcome = "timeout" if isinstance(error, TimeoutError) else "error"
            raise
        finally:
            elapsed = time.monotonic()-start
            if not slow_only or elapsed >= 1 or outcome != "ok":
                print(json.dumps({"stage": stage, "event": "end", "outcome": outcome,
                                  "seconds": round(elapsed, 3)}), flush=True)
    return invoke


def self_test():
    captured = io.StringIO()
    sentinel = object()
    failure = TimeoutError("never-log-this-secret")
    def fail(*args): raise failure
    with redirect_stdout(captured):
        assert observed("encoding", lambda value: value)(sentinel) is sentinel
        try: observed("browser", fail)("never-log-this-secret")
        except TimeoutError as error: assert error is failure
        else: raise AssertionError("exception swallowed")
    records = [json.loads(line) for line in captured.getvalue().splitlines()]
    assert len(records) == 4 and records[-1]["outcome"] == "timeout"
    assert "never-log-this-secret" not in captured.getvalue()
    print("PASS trace preserves results/exceptions and excludes arguments/messages")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        root = Path(sys.argv[1]).resolve()
        sys.path[:0] = [str(root/"test/e2e"), str(root/"python")]
        import m1_browser_test
        from smartdocs import vector_worker, search_http
        m1_browser_test.Browser.request = observed("browser_control",
            m1_browser_test.Browser.request, slow_only=True)
        vector_worker.request_encoding = observed("encoding", vector_worker.request_encoding)
        search_http.search_documents = observed("search_coordinator", search_http.search_documents)
        runpy.run_path(str(root/"test/e2e/hybrid_browser_test.py"), run_name="__main__")
