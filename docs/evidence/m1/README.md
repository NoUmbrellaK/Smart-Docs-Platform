# M1 acceptance evidence

See the [M1 gate coverage matrix](gate-matrix.md) for the requirement-to-test
mapping, M2+ deferrals, current evidence state, and closure blockers. The matrix
currently reports M1 as open.

Run the reproducible M1 acceptance command from the repository root:

```bash
test/e2e/run_m1.sh
```

The command is intentionally resource intensive. It defaults to one build job
and rejects `SMARTDOCS_BUILD_JOBS` values above the smaller of the online CPU
count and two. Run only one full acceptance instance at a time. See the
[2026-09-15 resource-exhaustion incident](../../operations/2026-09-15-m1-acceptance-resource-exhaustion.md)
before running it on a small single-host deployment.

The runner records the checkout HEAD and dirty state, runs the focused harness
self-test, then performs a clean build of `server`, `admin`, and `test-server`
plus `bin/smartdocs_tests` from that exact checkout before it starts any
acceptance service. After migrating the isolated database, it runs the C++
unit/integration binary with `SMARTDOCS_TEST_MYSQL=1`, the Python UI contract
suite, and the Node UI behavior suite. It runs all three before evaluating
their captured status and count output. A failure, skip, zero-test suite, or
malformed/inconsistent terminal counts make the M1 run fail; on failure the
runner prints each suite's status and captured output before cleanup. It
creates one private temporary root containing its isolated
MySQL data and Unix socket, controlled file storage, server logs, and scratch
evidence. It exercises `bin/smartdocs_test_server` through real loopback TCP
sockets and removes only that temporary root after stopping the MySQL PID it
captured. Before the fault scenarios, it briefly starts `bin/server` as a
production-binary smoke check; the production binary never receives a
fault-injection setting. Each Python scenario driver owns every test-server
child it starts, stops, crashes, or restarts.

Between HTTP T-01 through T-09 and the interruption rounds, the runner starts
a fresh `smartdocs_test_server` and runs the standard-library-only F-02.9
measurement. Four 32 MiB deterministic disk fixtures are uploaded in 8 MiB
parts, completed, and downloaded concurrently through real loopback sockets.
The client streams file I/O, verifies every final SHA-256, and reads the server
process's Linux `/proc/<pid>/status`. Final peak `VmHWM` minus baseline
`VmRSS` must be strictly below 64 MiB; baseline `VmHWM` is also recorded.
Missing or malformed RSS fields,
incorrect statuses or hashes, an exited server, malformed evidence, and values
at or above the limit fail the full runner.

On success, `latest/` contains four review inputs:

- `environment.txt`: sorted `key=value` facts for the commit, dirty state,
  operating system, CPU count, Python, Node, compiler, MySQL client/server,
  OpenSSL, and isolated transports.
- `results.json`: the machine-readable evidence described below.
- `streaming.json`: the standalone validated F-02.9 measurement, including
  dimensions, phase names, hashes, baseline RSS/high-water values, final peak,
  growth limit, status, and assertion count.
- `summary.md`: computed pass/fail/skip/total suite counts, HTTP and interruption
  pass denominators, assertion counts, fault distribution, failure details, and
  known gaps. It is not a stage-gate approval by itself.

`latest/results.json` uses aggregate schema version 2 and has this shape:

- `commit`: Git `head` and `dirty` state remeasured after all scenarios.
- `build`: the checkout metadata used for the clean build plus SHA-256 hashes
  of the executed server, admin, test, and test-server binaries. The run fails
  if checkout metadata changes between build and final evidence.
- `environment`: transport and dependency facts plus the measured compiler,
  MySQL client/server, OpenSSL, Node, Python, operating-system, and CPU
  versions/facts.
- `summary`: assertion, scenario, and interruption counts computed by the run.
- `supporting_suites`: runtime `passed`, `failed`, `skipped`, and `total` counts
  for `cpp_unit_integration`, `ui_contract`, and `ui_behavior`.
- `streaming_measurement`: the validated contents of `streaming.json`; a
  missing, malformed, failing, or at/over-limit result cannot be aggregated as
  a passing run.
- `http_scenarios`: T-01 through T-09 M1 results and explicit M2/M4 deferrals.
- `fault_distribution`: the measured `4/4/4/3/3/2` fault-point distribution.
- `interruption_rounds`: twenty records containing fault point, process exit,
  restart/task state, confirmed parts, result IDs, final SHA-256,
  duplicate-version count, singleton graph counts, assertions, and pass/fail.
- `failures`: explicit failure details; an accepted successful run has an empty
  list because failed drivers are never eligible for promotion.

`latest/` is generated and ignored. Trust it only after the runner exits zero
and its commit metadata matches the intended revision. Task 12 is responsible
for reviewing and copying an accepted run into a dated committed directory.
The evidence contains no passwords, cookies, uploaded bodies, logs, or
temporary absolute paths.

The F-02.9 measurement is wired and self-tested, but it was not executed on the
resource-constrained ECS while this change was prepared. Current-run evidence
therefore remains pending until the complete runner succeeds on a safe host.
