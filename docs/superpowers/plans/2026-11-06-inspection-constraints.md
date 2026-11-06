# Explicit task inspection constraints implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task. User approved A; do not ask another process choice.

**Goal:** Persist and enforce explicitly selected metadata search bounds, with an end-to-end task UI and runner path.
**Architecture:** C++ owns immutable constraints and enforces them in locked processing transactions. Python narrows available tools from the trusted StartRun result; UI submits a deliberate user selection. No natural-language parser or case-specific branch.
**Tech Stack:** Existing C++/MySQL17, Python MCP, native JS; no dependencies.
**Spec:** docs/superpowers/specs/2026-11-06-inspection-constraints-design.md

## Global Constraints

- Existing linked worktree /root/Smart-Docs-Platform/.worktrees/m1-file-business, branch codex/m3-mcp-agent; preserve unrelated evidence files.
- No real model/key/network calls. Keep original30 inputs/results unchanged. Existing production DB/service must not be changed.
- Builds/tests only serial current ECS smartdocsvectorcheck.slice: CPU100%, MemoryHigh1200MiB Max1600MiB Swap0 Tasks256 IO5MB/s 200IOPS; nobody:nogroup Nice10 UMask0077 NoNewPrivileges; runtime<=600s; IP denyany allowlocalhost.
- Main controller coordinates heavy commands; worker must not start them independently. Use apply_patch for edits. Incremental make -j1 only, no clean. Stop own test if available memory<512MiB.
- Runtime /tmp/smartdocs-safe.h4SzvX/repo; Python /tmp/smartdocs-mcp.P7grAI/venv/bin/python. Root owns private credentials; never inspect them.
- Lower-cost focused tests first, full relevant suites once. No force push. Main controller handles commits/push after review, workers leave changes uncommitted and report exact test commands/results.

### Task 1: C++ authoritative constraint contract

**Files:** db/migrations/017_task_inspection_constraints.sql; code/agent/agent_tasks.{h,cpp}; code/processing/document_search.cpp; code/processing/processing_service.cpp; code/app/application.cpp; scripts/test-with-mysql.sh; test/e2e/m3_inspection_constraints_test.py; docs/api/m3-task-inspection.md.
**Interfaces:** Optional create/Get/List/StartRun field `inspection_constraints={mode:"metadata",max_page:1..1000,page_size:1..10}`; absent for legacy. Shared C++ check after existing RequireAgentTaskScope while locks held. Forbidden calls return403/task_inspection_restricted; old audit catches it.

- [ ] Write real HTTP/SQL RED: create with `{mode:"metadata",max_page:1,page_size:10}` expects201, Get/List/StartRun preserves it; initial old server returns400. Ask controller to run before product changes.
- [ ] Implement migration nullable JSON and version17 startup guards/replay; exact validation as spec. No backfill. Add read-only helper for persisted policy and enforcement in processing calls.
- [ ] Extend focused tests: allowed first page; page2,keyword,hybrid,all read selectors rejected; rejected response contains no sample text and audit statusaccess_denied; max_page2/page_size2 allows exactlyvalid bounds; default10 rejected ifchosen2; owner isolation; cancellation; old task/read unchanged; restart preserves policy; malformed null/bool/float/extra/missing/zero/overflow rejected; report/archives remain unaffected.
- [ ] Migration proof: preexisting legacy task staysNULL after16->17, replay safe; no invented consent. Ask controller for incremental build+focused GREEN+existing task/read/report/audit suites; preserve failure logs.
- [ ] Self-review and write report with RED/GREEN commands, observed outputs, files, remaining risks. Do not commit; controller reviews and commits.

### Task 2: Python and user-facing selection

**Files:** python/smartdocs/agent_runner.py; test/mcp/test_agent_runner.py; test/e2e/m3_inspection_mcp_test.py; resources/app.html; resources/js/agent.js; test/e2e/agent_ui_test.mjs; test/e2e/m3_inspection_browser_test.py; docs/api/m3-task-inspection.md.
**Interfaces:** Consume Task1 field exactly, no client policy writes after creation. Keep absent-field behavior and global SCHEMAS unchanged.

- [ ] Add RED Python tests: metadata task model sees only search plus its existing terminal tool; search modeconst metadata, max page/size bounded, page_size required; force a read attempt and assert not dispatched; invalid trusted constraints terminate before model; next unrestricted run exposes normal schema. Example assertion `self.assertNotIn('read_document_version', names)`.
- [ ] Implement strict validation and copied per-run schemas, trusted system notice; no new model calls or permission reimplementation. Add real MCP check that direct read cannot bypass C++ restriction, valid metadata-only draft still saves.
- [ ] Add RED UI tests: choose mode+limits then re-consent and create; expect exact constraints payload; old default omits it; limit edits reset consent; invalid bounds prevent request; in-flight locks and draft/project reset; detail shows persisted restriction using textContent.
- [ ] Implement native labelled checkbox and numeric controls; mobile sizing consistent with existing form. No natural-language keyword interception, no extra tool, no archive confirmation.
- [ ] Run focused Python/Node suites and controller-run real MCP/HTTP integration serially; validate phone360/390/414,tablet768,desktop1280 layout when browser available, otherwise accurately state unverified visual gate.
- [ ] Self-review report, no commit; controller task review then final integration review, update README/evidence and normal commit/push. New capabilities do not change H05 verdicts.
