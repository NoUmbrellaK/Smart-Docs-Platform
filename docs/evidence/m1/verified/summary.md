# M1 acceptance summary

- Generated: 2026-10-17T08:25:11Z
- Commit: `ed14dcf6029e53ac7106847fa456b3067af0147c` (clean)
- Result: **PASS**
- HTTP scenarios: 9/9 passed (44 assertions)
- Browser scenarios: 2/2 passed (20 assertions); chrome 155.0.8059.84; WebDriver 155.0.8059.84; headless=new, sandbox=enabled
- Interruption rounds: 20/20 passed (305 assertions)
- Streaming measurement: PASS, peak growth 1515520/67108864 bytes (69 assertions)
- C++ unit/integration: 90/90 passed, 0 failed, 0 skipped
- UI contract: 8/8 passed, 0 failed, 0 skipped
- UI behavior: 18/18 passed, 0 failed, 0 skipped

## Fault distribution

- AfterPartTempFsync: 4
- AfterAssembledFsync: 4
- AfterObjectRename: 4
- BeforeDatabaseCommit: 3
- AfterDatabaseCommit: 3
- BeforeHttpResponse: 2

## Failures

- None.

## Known gaps

- M2 parsing, metadata extraction, and index publication are outside M1.
- M4 load, soak, and concurrency measurements are outside this run.
- An operator must review `latest/` before promoting it to `verified/`.
