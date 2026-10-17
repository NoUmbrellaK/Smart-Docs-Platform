# M1 acceptance evidence

See the [M1 gate coverage matrix](gate-matrix.md) for the requirement-to-test
mapping, M2+ deferrals, current evidence state, and closure blockers. The matrix
currently reports M1 as open with two explicit closure blockers: the accepted
current-HEAD full artifact set, and runtime gate proof/final review.

Run the reproducible M1 acceptance command from the repository root:

```bash
test/e2e/run_m1.sh
```

The command is intentionally resource intensive. It defaults to one build job
and rejects `SMARTDOCS_BUILD_JOBS` values above the smaller of the online CPU
count and two. Run only one full acceptance instance at a time. See the
[2026-09-15 resource-exhaustion incident](../../operations/2026-09-15-m1-acceptance-resource-exhaustion.md)
before running it on a small single-host deployment.

The browser scenarios require a Chrome/Chromium executable with its native PDF
viewer and a compatible ChromeDriver executable (matching major version).
Set `SMARTDOCS_BROWSER_BIN` and `SMARTDOCS_WEBDRIVER_BIN` to explicit executables,
or let the driver discover `chromium`, `chromium-browser`, `google-chrome`, and
`chromedriver` on PATH. These are test-host dependencies only; no pip/npm browser
package is needed. The runner resolves and queries them before build/MySQL work.
Run as a non-root user with the browser sandbox enabled. A disposable root
acceptance host may explicitly set
`SMARTDOCS_BROWSER_ALLOW_ROOT_NO_SANDBOX=1`; only that root opt-in adds
`--no-sandbox`, and evidence records the reduced browser-process isolation.
Same-origin, certificate, and web security checks are never disabled.
The UI suite also needs Node with `node:test`, global `File`, and ES module
loading (validated with Node 24.18.0). A capability probe rejects an unsupported
Node before any build or MySQL startup. Non-interactive systemd services do not
inherit an interactive shell's nvm selection; set their PATH explicitly.
Creating the WebDriver session allows up to 120 seconds for cold browser loading
under disk I/O limits. Other WebDriver requests retain their 30-second timeout;
timeouts propagate without automatically replaying browser actions.

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

After HTTP T-01 through T-09, the standard-library browser driver starts its own
test server and ChromeDriver children and drives the real workbench using the
W3C WebDriver HTTP protocol in `--headless=new` mode. It logs in through visible
controls, explicitly reloads a task with part 0 already confirmed, rejects a
same-size wrong file before any part PUT, then reselects the original file and
requires PUTs for exactly the missing parts, stable completion IDs and the final
download SHA-256. File selection uses WebDriver's real file-input operation.
JavaScript is used only to inspect visible DOM state. Performance events are
processed in memory; only observed part numbers enter evidence. The mismatch
phase must observe the task GET as well as zero part PUTs.

The PDF scenario uploads the same deterministic two-page fixture used by HTTP
T-05, checks authentication and exact bytes, then opens it through the workbench
and submits the visible preview form for pages 1 and 2. Each iframe screenshot
must first contain the requested page in the central third of the document
viewport (both axes) before it starts or advances the three-sample stabilization
count, so identical blank or non-target frames cannot satisfy stabilization. It
must be a complete CRC/zlib-valid PNG with nontrivial size/dimensions. This
region excludes the top toolbar and
left thumbnails in the fixed CI layout. It must contain at least 20 percent
(and 1000 opaque pixels) of the requested page's red/blue color and at most
1 percent of the other page's color; mixed-page viewports fail. Both full-image
SHA-256 and decoded document-region SHA-256 must differ between pages, so a
toolbar/page-indicator change cannot substitute for navigation. The document
hash covers big-endian uint32 region width/height followed by row-major RGB
pixels, after PNG filter decoding; it excludes viewer chrome and PNG encoding.
The exact `#page=1` and `#page=2` sources must bind the same protected version.
A URL-fragment or Range-only result is insufficient. Review both retained PNGs
before accepting the run. Browser/WebDriver processes and the profile are owned
by this driver; no process-name kill or broad cleanup is used.

After the browser driver and before interruption rounds, the runner starts
a fresh `smartdocs_test_server` and runs the standard-library-only F-02.9
measurement. Four 32 MiB deterministic disk fixtures are uploaded in 8 MiB
parts, completed, and downloaded concurrently through real loopback sockets.
The client streams file I/O, verifies every final SHA-256, and reads the server
process's Linux `/proc/<pid>/status`. Final peak `VmHWM` minus baseline
`VmRSS` must be strictly below 64 MiB; baseline `VmHWM` is also recorded.
Missing or malformed RSS fields,
incorrect statuses or hashes, an exited server, malformed evidence, and values
at or above the limit fail the full runner.

On success, `latest/` contains these review inputs:

- `environment.txt`: sorted `key=value` facts for the commit, dirty state,
  operating system, CPU count, Python, Node, compiler, MySQL client/server,
  OpenSSL, browser/WebDriver executable basenames and product versions,
  headless/sandbox modes, and isolated transports.
- `results.json`: the machine-readable evidence described below.
- `streaming.json`: the standalone validated F-02.9 measurement, including
  dimensions, phase names, hashes, baseline RSS/high-water values, final peak,
  growth limit, status, and assertion count. All schema, dimension, phase,
  RSS, and assertion numeric fields require exact JSON integers; floats and
  booleans are rejected.
- `browser.json`: exact schema version 2, overall `status`, positive integer
  `assertions`, `browser`/`webdriver` executable basenames and numeric product
  version strings, `headless`, `sandbox`, and exactly two named `scenarios`:
  `refresh_resume_reselect` and `protected_pdf_page_navigation`. The former
  records confirmed/missing/observed PUT part sets, byte counts, reload count,
  source/download SHA-256 and stable file/version/job IDs; the latter records
  fixture SHA-256 and two page numbers, exact protected relative API sources,
  screenshot filenames, byte sizes, whole-image `sha256` and document-region
  `document_sha256` values. Both hashes are recomputed from the retained PNGs;
  schema-1 browser results are rejected. All numeric schema
  fields require exact JSON integers; floats and booleans are rejected.
- `browser-artifacts/pdf-page-1.png` and `pdf-page-2.png`: actual iframe images,
  validated against `browser.json` both before interruption and after copying
  from the private scratch evidence directory.
- `summary.md`: computed pass/fail/skip/total suite counts, HTTP and interruption
  pass denominators, assertion counts, fault distribution, failure details, and
  known gaps. It is not a stage-gate approval by itself.

`latest/results.json` uses aggregate schema version 4 and has this shape:

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
  a passing run. Each phase records configured concurrency, measured maximum
  overlapping request calls, and request count; observed concurrency below
  four fails the measurement.
- `browser`: the strictly validated contents of `browser.json`. Missing or
  malformed/failing evidence, unexpected fields, secrets/absolute filesystem
  paths, integer impostors, missing/duplicate/replayed part PUTs, wrong hashes,
  fragments, trivial/invalid/missing/identical screenshots or absent rendered
  page-specific document color markers or unchanged document fingerprints fail
  before interruption starts. The browser PDF hash must
  match the HTTP fixture hash. Driver nonzero exits stop before streaming or
  aggregation. Existing supporting-suite, HTTP, streaming and interruption
  gates and checkout HEAD/dirty binding remain in force.
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

The F-02.9 measurement and real-browser M1 scenarios remain unexecuted on this
ECS. On 2026-10-17, after the user required execution on the same server, kernel
resource-limit probes and a non-root, sandbox-enabled browser startup smoke
check succeeded. The cold-start timeout regression and other lightweight harness
tests passed (23/23). This is not business-scenario or PDF screenshot evidence.

The [same-host limited-run procedure](../../operations/2026-10-17-m1-same-host-limited-run.md)
replaces the earlier blanket prohibition with an isolated, resource-capped
execution procedure. After explicit risk acceptance, the first bounded run built
all targets and reached supporting suites, then failed closed on a configuration
test's inherited environment and systemd's old Node runtime. Both causes were
corrected and checked independently; see the run record for exact counts.
No passing full-run result was generated. Do not invoke the uncapped runner
directly on the affected host. Browser and streaming runtime proof remain part
of the full-artifact blocker.
