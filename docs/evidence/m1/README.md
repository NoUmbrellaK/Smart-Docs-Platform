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

On success, `latest/` contains three review inputs:

- `environment.txt`: sorted `key=value` facts for the commit, dirty state,
  operating system, CPU count, Python, Node, compiler, MySQL client/server,
  OpenSSL, and isolated transports.
- `results.json`: the machine-readable evidence described below.
- `summary.md`: computed pass/fail/skip/total suite counts, HTTP and interruption
  pass denominators, assertion counts, fault distribution, failure details, and
  known gaps. It is not a stage-gate approval by itself.

`latest/results.json` has this schema:

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
