# Resident encoder supervision implementation plan

> **For agentic workers:** Use executing-plans inline; keep heavy tests serial.

**Goal:** Expose the verified resident core over the existing private Unix protocol,
with independent bounded startup and request lifetimes and complete child cleanup.

**Architecture:** One lightweight supervisor owns the existing model flock and Unix
listener. One isolated subprocess loads the fixed model once and exchanges bounded
length-prefixed messages over an inherited socketpair. The listener is created only
after a ready frame. Each accepted request has one 60-second deadline covering input,
IPC and output; competing connections receive busy after at most one second of bounded
input handling within the active deadline (slow/oversized competitors are closed). Timeout or IPC corruption
attempts to kill/reap the child and ends the service; no request replay or automatic restart.
Reaping waits at most five seconds; uninterruptible kernel IO is explicitly an
unreaped failure requiring an empty cgroup check before any manual restart.

**Tech Stack:** Python standard library, existing ResidentEncoder and systemd caps.

**Spec:** requirements R-02/R-03, section 10 and T-19; verified lifecycle boundaries in
`docs/evidence/m2/resident-core/README.md`. This reversible opt-in service does not
activate itself, change business authorization or replace the old entrypoint.

## Global constraints

- Non-root, kernel AF_UNIX-only network isolation, original shared memory/CPU/IO caps.
- Input 64 KiB, output 2 MiB, 8 chunks/1200 characters, existing revision unchanged.
- Startup deadline 180 seconds, separate from the unchanged 60-second request limit.
- Child virtual memory 2 GiB; startup memfd file bound 256 MiB, then 2 MiB;
  core dumps disabled, inherited tighter limits never relaxed.
- No lifetime 60-second CPU limit on a reused process: per-request wall deadline and
  shared CPU quota bound work instead. Parent remains lightweight, child env whitelisted.
- Parent directory 0700 and socket 0600 owned by the service account. Existing socket
  paths are refused, not blindly unlinked. Normal stop removes only the socket we bound.
- The deployment service must kill the whole cgroup on termination, Restart=no.
- No credentials/model paths/identity accepted in request JSON. C++ still authorizes
  before encoding and before publication/search return. No DeepSeek calls.

## Task 1: Protocol and lifecycle

Files: create `python/smartdocs/resident_service.py`,
`test/python/test_resident_service.py`; reuse `embedding_service.handle_request`,
`worker.worker_lock`, `vector_worker.request_encoding` unchanged.

Interfaces: `serve(path, model_dir, python_executable, lock_path,
startup_seconds=180, request_seconds=60)`; internal framing uses 4-byte big-endian
length then bytes, validates lengths before allocation. Test substitutes only child
model construction/encoding through a test subprocess fixture, never supervisor IO.

- [x] Write tests for ready-before-listen, repeated same-child requests, busy response,
  input/output bounds, startup and native hang timeouts, crash, signal cleanup, shared
  lock and unsafe/existing socket refusal. A missing service must fail first.
  Use `with self.assertRaises(EmbeddingError)` for the existing client and process PID
  disappearance plus socket removal to prove cleanup; fake vectors are explicitly
  transport fixtures and not model accuracy evidence.
- [x] Run `PYTHONPATH=python python -B -m unittest discover -s test/python
  -p test_resident_service.py -v`, record red, then implement the minimal supervisor.
- [x] Run targeted and full Python tests in the original capped non-root test unit.

## Task 2: Real service, docs and handoff

Files: create `test/e2e/m2_resident_service_test.py`, update
`docs/deployment/m2-vector-worker.md`, README and evidence directory.

- [x] Add a real non-root/offline socket test, using the original client and fixed
  old-revision fingerprint. Send multiple requests through one model process, verify
  invalid input is safe, terminate the supervisor and prove model process is gone.
- [x] Run under original cgroup caps with at most one model process; inspect memory
  events and host headroom. No browser combination until lifecycle tests pass.
- [x] Request independent read-only review, address important findings; record actual
  timings, source hashes, failures and resource cleanup. Document Type=notify readiness,
  TimeoutStartSec, shared lock, Restart=no and manual stale-socket recovery.
- [ ] Commit and normal push after tests. Keep M2 unaccepted until the real browser
  combination and remaining M2 gates are actually passed.

Verification outcome: final Python 95/95 and real private model service 1/1 with six
checks pass; no additional memory.high/OOM events. Review has no remaining important
findings. See `docs/evidence/m2/resident-service/README.md`; git commit/push is the
handoff action, not a claim that M2 or production deployment is complete.
