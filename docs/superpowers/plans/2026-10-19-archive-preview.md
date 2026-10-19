# Archive Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现方案A首个可验证增量：管理员目标批准和无移动副作用的持久归档预览。
**Architecture:** 现有C++业务事务保存权威状态，复用任务范围及项目角色校验；不接MCP写入口。
**Tech Stack:** C++14、MySQL8、nlohmann JSON、Python unittest真实HTTP测试。
**Spec:** `docs/superpowers/specs/2026-10-19-archive-preview-design.md`

## Global Constraints

只迁移隔离测试数据库，不迁移生产、不外发模型。
CPUQuota100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、TasksMax256、/dev/vda双向5MB/s及200IOPS。
单进程构建，MemAvailable低于512MiB停止本次测试。保持既有隔离worktree，不切换main。

### Task 1: 真实HTTP回归与策略/预览实现

**Files:** 新增 `code/agent/archive_plans.cpp`、`code/project/archive_policy.cpp`、
`db/migrations/009_archive_previews.sql`、`test/e2e/m3_archive_preview_test.py`；修改
`code/agent/agent_tasks.{h,cpp}`、`agent_routes.cpp`、`code/project/project_service.{h,cpp}`、
`project_routes.cpp`、`code/app/application.cpp`、迁移测试及数据库重置。

**Interfaces:** ProjectService::ArchivePolicy(session,project)、SetArchivePolicy(session,project,body,request_id)、
静态ApprovedArchivePolicy(connection,project,revision)；AgentTaskService::SaveArchivePlan(session,project,task,body,request_id)、
GetArchivePlan(session,project,task)。RequireArchiveTaskScope(connection,session,project,task,writing)复用现有Scope。

- [x] 写真实HTTP测试；关键断言如下，使用TaskListE2E的上传、策略和任务fixture，禁止模拟业务后端：
  ```python
  saved = data(self.user.request("PUT", path, body))
  self.assertEqual(saved["state"], "preview")
  self.assertFalse(saved["executable"])
  self.assertEqual(data(self.user.request("PUT", path, body)), saved)
  self.assertEqual(self.file(job)["directory_id"], source)  # 通过真实文件列表查稳定ID
  ```
- [x] 在受限单元内运行 `scripts/test-with-mysql.sh -- <parser-python> -B test/e2e/m3_archive_preview_test.py`；
  确认缺失HTTP路由导致预期200实际404的RED，保存日志。
- [x] 新增schema9两个表；策略行用项目锁保护首次创建，方案行用任务锁保护首次创建。
  设置/方案保存按以下顺序执行：
  ```cpp
  MySqlTransaction transaction(db);
  // plan: membership -> queue/task -> scope files -> plan -> project/policy -> directories/files
  // policy/rename: membership -> project -> policy -> directories
  // exact request validation -> full scope -> current snapshots -> expected revision
  // equal replay returns existing JSON; stale/different request throws 409
  // INSERT/UPDATE preview + InsertAudit, never UPDATE files
  transaction.Commit();
  ```
  在现有目录改名事务先锁项目，命中目标时撤销批准；服务端ID和预览字段拒绝由请求指定。
- [x] 增加schema门槛9、重放和升级空表断言及重置顺序；增量构建
  `make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin`，跑上述HTTP测试和既有m3 C++测试。

### Task 2: 审查、证据和发布

**Files:** `README.md`、`docs/api/m3-archive-preview.md`、`docs/evidence/m3/archive-preview/`、本计划。
**Interfaces:** 文档明确HTTP输入/返回、错误码、schema9、未接入MCP/执行/网页的边界。

- [x] requesting-code-review独立只读审查同时整理证据；未发现严重/重要问题，补充轻微测试缺口与锁顺序说明。
- [x] verification-before-completion：受影响HTTP9/9、C++31/31和迁移检查通过；检查限额/oom计数。
- [x] 记录RED/GREEN/源文件哈希和无生产变更；更新README，不把整个M3/M4标为完成。

发布操作：核对远程与分支，提交精确文件并正常推送，验证本地与远程HEAD一致；结果以Git记录为准。
