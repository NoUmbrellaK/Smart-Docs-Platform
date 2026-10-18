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

- [ ] 添加真实 Application 请求辅助函数和边界用例。关键对照：
  ```cpp
  CHECK(RemoteSearch(app, f, {{"mode","metadata"},{"query",""}}).at("items").empty());
  CHECK(Search(app, f, {{"mode","metadata"},{"query",""}}).at("items").size() == 1);
  ```
- [ ] 受限单元增量构建测试目标；隔离 MySQL 执行 `bin/smartdocs_tests --filter=m3_remote`，确认失败来自新路由缺失。
- [ ] 搜索增加可信 bool，按 `hybrid || remote_ai` 限制 SQL scope，remote_ai 时 page_size 上限 10；普通接口语义不变。
- [ ] ReadDocument 文件/版本锁定查询读取 policy/approved，remote_ai 且任一不满足返回统一 resource_not_found；复用现有 chunk 返回逻辑。
- [ ] 注册 remote-ai 路由，保留认证/同源/JSON 上限，新只读 GET 拒绝 query/body；模型不能关闭门禁。
- [ ] 重跑同一测试；再执行完整 C++ 套件，检查失败/跳过及限额事件，不把跳过当通过。
- [ ] 审阅安全边界、记录实际证据，更新 README/API，核对 remote/branch 后提交推送；不合并主分支。
