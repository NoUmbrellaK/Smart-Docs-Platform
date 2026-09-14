# M1 acceptance evidence

Run the reproducible M1 acceptance command from the repository root:

```bash
test/e2e/run_m1.sh
```

The runner creates one private temporary root containing its isolated MySQL
data and Unix socket, controlled file storage, server logs, and scratch
evidence. It starts only `bin/smartdocs_test_server`, exercises the service
through real loopback TCP sockets, and removes only that temporary root after
stopping the MySQL PID it captured. Each Python scenario driver owns every
test-server child it starts, stops, crashes, or restarts.

On success, `latest/results.json` has this schema:

- `commit`: exact Git `head` and the measured `dirty` flag.
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
