# Explicit task inspection constraints (approved A)

Base: `910af8c2182363f9c1ef10109810b257c0f73866`. Offline-only increment on the existing ECS.
User approved an explicit task option with authoritative server enforcement.
The earlier [characterization](../inspection-boundary-20261106/README.md) records why
natural-language requests alone were not a hard boundary. Original H05 inputs/results
and all business/model usage ledgers remain unchanged.

The optional immutable `inspection_constraints` is persisted by C++ (schema17).
Constrained tasks permit metadata searches within the chosen per-request page bounds,
but no status/chunk/range read tool. Existing project/owner/version/AI checks still apply.
Ordinary file viewing/downloads and real archive confirmation remain separate.
Page bounds do not impose a cumulative unique-record budget across distinct queries.

## Verification record

- RED: one test failed at create, expected201/actual400; [log](../inspection-boundary-20261106/red.log).
- Incremental `make -j1 bin/server bin/smartdocs-admin`: exit0, no compiler warnings; [log](build.log).
- Initial GREEN stopped in the migration fixture before HTTP tests: adding an intentional legacy task
  invalidated the old zero-task assertion. [Failure preserved](initial-green.log).
  Test-only correction checks exactly that legacy identity, NULL policy, no invented version scope,
  and the existing guard; no product change or relaxed policy.
- Corrected focused HTTP/SQL: **6/6**, 6.366s, exit0; [log](green.log).
  Includes malformed API/SQL inputs, persistence/StartRun/list/restart, page bounds/defaults,
  all forbidden tool selectors, content-free access_denied audit, ordinary routes,
  legacy behavior, owner/cancel checks, and report/plan compatibility.

The complete offline verification record follows. None of these tests is a real-model
or business acceptance result.

Existing task-list regression: **3/3**, 3.884s ([log](tasklist.log)); report HTTP/SQL
regression: **15/15**, 35.363s ([log](reports.log)); write-tool audit (including read-tool
audit methods): **12/12**, 24.294s ([log](writeaudit.log)). These plus focused6 are
36 distinct test methods. Task1 independent code review found no spec or quality issues;
task2 integration is a separate gate. No rebuilt C++ unit-test binary was run in this increment.

Before Python changes, existing report MCP regression passed **4/4**, 8.043s
([log](reportmcp.log)), using the local scripted transport, not DeepSeek.
Task2 RED: Python 3 methods, 11 failed assertions in 1.782s ([log](python-red.log));
UI 4/4 failed in 579.684ms ([log](ui-red-fixed.log)). The first UI invocation did not
execute tests: systemd resolved root's inaccessible nvm Node (203/EXEC), recorded in
[initial log](ui-red.log). The rerun used the existing test-only Node v24.18.0; no
permissions were loosened and no dependency was installed.

Before syncing product changes, the terminal-tool test was strengthened to assert
captured tool names outside the fake model callback; integral float cases were added.
The stronger RED ran 3 methods in 1.832s with 15 failed assertions
([log](python-red-fixed.log)). This avoids accepting assertions swallowed as model errors.

Task2 GREEN: full Python MCP/runner suite **133/133**, 90.486s ([log](python.log));
full Agent UI suite **58/58**, 2588.084835ms ([log](ui.log)), no skips.
The three Python client warnings about unlisted write tools are from existing negative
tests in `test_mcp_read.py` (non-task scopes must reject those calls without a backend
request); they are not new product validation bypasses. Raw warnings are retained.

Real MCP/HTTP/isolated SQL: **2/2**, 4.054s ([log](mcp.log)); direct MCP reads cannot
bypass C++ denial, while a scripted two-turn metadata-only run saves an uncertain
report draft with no content reads. The fake model transport sends no paid requests.
Task2 independent spec/code review approved the frozen implementation with no findings;
the browser gate was then completed as recorded below.

First browser gate failed before any page interaction: ChromeDriver could not create a
session (one test error, 120.978s; [log](browser.log)). The fixture did not preserve a
driver log, so the exact initial cause is unknown. A same-limit, sandbox-enabled
Chrome-only [probe](browser-probe.py) then passed ([log](browser-probe.log)); it accesses
no SQL or application. This proves the browser can start, not that the page passes.
Only the fixture's existing optional `driver_log` argument was added; its scoped
re-review found no new issues. No timeout, sandbox, product or assertion was relaxed.
One complete retry passed **1/1**, 58.834s ([log](browser-fixed.log)), with actual
Chrome/C++/isolated SQL. It verifies choice/persistence/detail, re-consent, reset,
legacy omission and native invalid-bound refusal; widths 360/390/414/768/1280 all pass
the form overflow and control-height checks. Controller visually inspected the 360/1280
screenshots: new fields remain legible, and the invalid1001 state is correctly rejected.
Screenshots and private driver diagnostics are identified by [hashes](browser-artifact-hashes.txt);
the driver log is not published because it contains synthetic login/session traffic.
The separate [browser preflight](browser-preflight.txt) and [postflight](postflight.txt)
are identical across 211 files/two binaries; only the diagnostic fixture line differs
from the first preflight. Final broad review found no actionable issues.

Final passing suites total **234 test-method executions**: HTTP/SQL36, prior reportMCP4,
Python133, UI58, new MCP2, browser1. This excludes initial RED/errors, the characterization,
the Chrome-only probe, and repeated migration assertions; it is not 234 business cases.
The reportMCP4 run preceded the Python changes; the full Python suite and new MCP2 ran
afterward. Test boundaries and initial failures remain visible above.

## Safety and reproduction

Commands: [run.sh](run.sh); each SQL suite gets a disposable MySQL instance with no TCP listener.
All heavy steps are serial in `smartdocsvectorcheck.slice`, CPU100%, MemoryHigh1200MiB,
Max1600MiB, Swap0, Tasks256, disk read/write5MB/s and200IOPS, nobody:nogroup,
Nice10, UMask0077, NoNewPrivileges, runtime600s, localhost-only network.
The initialization warning about an empty root password belongs to this isolated socket-only
test database, not the production database. No provider credentials or real model are accessed.

Final [resource record](final-resources.txt): slice tasks0; memory.events high82471,
max/oom/oom_kill0, unchanged from baseline. Actual limits are in [resource-limits.txt](resource-limits.txt).
No production migration, service restart, real archive confirmation or business-ledger update
was performed. Evidence names use the November6 work-start date; the final suites ran after
midnight November7 Asia/Shanghai, as their journal timestamps show.
Raw Node RED output retains eight whitespace-only lines exactly as captured; therefore
`git diff --check` is clean for source/docs with raw `*.log` evidence excluded, not for
the untouched raw logs themselves.

`backend-preflight.txt` binds the C++/SQL/Python code, selected existing tests,
new focused test and two binaries. These are protocol/authorization tests, not proof
of model semantic compliance, business success or production deployment.
