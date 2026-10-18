# M3 只读外发门禁实施计划

> **For agentic workers:** Use superpowers:executing-plans to implement this single increment task-by-task.

**Goal:** 为后续 MCP 搜索/读取提供 C++ 权威外发检查，保留普通阅读能力。

**Architecture:** 复用 ProcessingService 的既有事务，新增由路由绑定的 remote_ai 参数；绝不从 JSON 读取此开关。独立路径的所有模式在 SQL 中过滤批准，读取在锁内拒绝未批准版本。

**Tech Stack:** 现有 C++14、MySQL、nlohmann JSON、Application 集成测试；无新依赖。

**Spec:** `docs/superpowers/specs/2026-10-18-m3-mcp-agent-design.md`

## Global Constraints

- 默认/最多 10 项，单片段最多 1200 Unicode 字符。
- 当前服务器串行增量构建 -j1，禁止 make clean。
- 不读取 DeepSeek 密钥，不调用模型，不改业务库。
- 此计划只覆盖设计中的首个增量，不代表 M3 完成。

## Task 1: 强制外发范围的搜索与固定片段读取

**Files:** 修改 `code/processing/{processing_service.h,processing_service.cpp,document_search.cpp,processing_routes.cpp}`；测试 `test/integration/processing_jobs_test.cpp`；接口说明 `docs/api/m3-remote-read-api.md`。

**Interfaces:** `Search(session, project, request, bool remote_ai=false)`；`ReadDocument(session, project, file, version, chunk_id="", bool remote_ai=false)`。原调用保持默认 false，新 remote-ai 路由绑定 true。

- [x] 添加真实 Application 请求辅助函数和边界用例。关键对照：
  ```cpp
  CHECK(RemoteSearch(app, f, {{"mode","metadata"},{"query",""}}).at("items").empty());
  CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}).at("items").size() == 1);
  ```
- [x] 受限单元增量构建测试目标；隔离 MySQL 执行 `bin/smartdocs_tests --filter=m3_remote`，确认失败来自新路由缺失。
- [x] 搜索增加可信 bool，按 `hybrid || remote_ai` 限制 SQL scope，remote_ai 时 page_size 上限 10；普通接口语义不变。
- [x] ReadDocument 文件/版本锁定查询读取 policy/approved，remote_ai 且任一不满足返回统一 resource_not_found；复用现有 chunk 返回逻辑。
- [x] 注册 remote-ai 路由，保留认证/同源/JSON 上限，新只读 GET 拒绝 query/body；模型不能关闭门禁。
- [x] 重跑同一测试；分组执行完整 C++ 套件，检查失败/跳过及限额事件，不把跳过当通过。最终 147/147，名称并集无遗漏/重复，见 docs/evidence/m3/remote-read/README.md。
- [x] 审阅安全边界、记录实际证据，更新 README/API，核对 remote 为 NoUmbrellaK/Smart-Docs-Platform、branch 为 codex/m3-mcp-agent。正常提交推送作为交接动作，以最终 git 状态为准；不合并主分支。

## 执行调整

首次将增量编译与完整套件放在同一 900 秒单元，触及墙钟上限，缺少完整汇总，不算通过。日志保留在 `docs/evidence/m3/remote-read/combined-timeout-journal.txt`。已观察到测试等待隔离库 TRUNCATE 的提交，memory high/max/oom/oom_kill 无新增；不据此证明长期无资源风险。

改为单独受限编译，再按全部 TEST_CASE 名称排序、交替分入 74/73 两组串行执行。每个完整名称均验证不与其他用例名称构成子串匹配，避免既有 --filter 语义导致重复。每组通过原 scripts/test-with-mysql.sh 建立隔离库，逐项执行 `stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case"`，遇失败即停止。最终必须核对两组实际 PASS 名称的并集等于源码全部 147 项，且无失败/跳过；不能只相加行数。资源上限与断言不变。

静态审查未发现 Critical/Important；补充一个 metadata/keyword 候选选出后撤销批准的回归，用于验证返回前重检，未因此修改生产代码。该用例属于对已有实现的审查补验，不伪称它在本次经历新的生产 RED/GREEN 修复。

补验首次实际运行失败：测试只清除了 remote_ai_approved，却未清空批准人/时间，违反 ck_file_version_remote_approval。改用现有 FileService::SetRemoteAiPolicy 的真实事务撤销该版本批准，同时保留文件 remote_ai_allowed 策略，避免手写不完整状态。原失败、仅修改此用例的 diff 均归档；不改生产代码或放宽断言。第一组不包含该用例，第一组的实现/被测用例均未变化；修正后第二组须完整重跑，不能将前次 25 个通过另行累计。
