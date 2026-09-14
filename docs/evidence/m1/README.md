# M1 acceptance evidence

Run the reproducible M1 acceptance command from the repository root:

```bash
test/e2e/run_m1.sh
```

The runner records the checkout HEAD and dirty state, runs the focused harness
self-test, then performs a clean build of `server`, `admin`, and `test-server`
from that exact checkout before it starts any acceptance service. It creates
one private temporary root containing its isolated MySQL
data and Unix socket, controlled file storage, server logs, and scratch
evidence. It starts only `bin/smartdocs_test_server`, exercises the service
through real loopback TCP sockets, and removes only that temporary root after
stopping the MySQL PID it captured. Each Python scenario driver owns every
test-server child it starts, stops, crashes, or restarts.

On success, `latest/results.json` has this schema:

- `commit`: Git `head` and `dirty` state remeasured after all scenarios.
- `build`: the checkout metadata used for the clean build plus SHA-256 hashes
  of the executed admin and test-server binaries. The run fails if checkout
  metadata changes between build and final evidence.
- `environment`: transport and dependency facts without local paths or secrets.
- `summary`: assertion, scenario, and interruption counts computed by the run.
- `http_scenarios`: T-01 through T-09 M1 results and explicit M2/M4 deferrals.
- `fault_distribution`: the measured `4/4/4/3/3/2` fault-point distribution.
- `interruption_rounds`: twenty records containing fault point, process exit,
  restart/task state, confirmed parts, result IDs, final SHA-256,
  duplicate-version count, singleton graph counts, assertions, and pass/fail.

`latest/` is generated and ignored. Trust it only after the runner exits zero
and its commit metadata matches the intended revision. Task 12 is responsible
for reviewing and copying an accepted run into a dated committed directory.
The evidence contains no passwords, cookies, uploaded bodies, logs, or
temporary absolute paths.
