# Archive Confirmation and Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户确认绑定具体预览，执行整批全成/全败并可安全重放已提交结果。
**Architecture:** 复用C++/MySQL、项目权限、任务锁、目标批准、来源门禁；确认/执行是独立同源业务接口。
**Tech Stack:** C++14/MySQL8/native JS/Python真实HTTP测试。
**Spec:** `docs/superpowers/specs/2026-10-19-archive-execution-design.md`

## Global Constraints

CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、磁盘双向5MB/s/200IOPS。
单线程构建和串行测试；MemAvailable低于512MiB停止本轮测试。只迁移临时库，无模型外发。
沿用现有worktree及已批准方案A，不合并main、不部署生产；MCP写工具和网页确认另行联调。

### Task 1: 失败测试与事务实现

**Files:** 新增 `test/e2e/m3_archive_execution_test.py`、`db/migrations/010_archive_execution.sql`；
修改 `code/agent/{archive_plans.cpp,agent_tasks.h,agent_tasks.cpp,agent_routes.cpp}`、
`code/app/application.cpp`、schema测试/迁移wrapper/重置表清单、既有预览测试的任务状态断言。

**Interfaces:** AgentTaskService::DecideArchivePlan(session,project,task,body,request_id)、
ExecuteArchivePlan(session,project,task,body,request_id)、ArchiveResult(session,project,task)。
LockArchiveTask(connection,session,project,task)返回当前状态；
RequireArchiveTaskScope(connection,session,project,task,writing,exclusive=false)复用Scope，执行时独占文件锁。

- [x] 先写HTTP测试，从既有ArchivePreviewE2E继承仅夹具，手工构造两个文件及一个目标：
  ```python
  self.assertEqual(client.request("POST", path+"/execute", unconfirmed).status, 409)
  confirmed = data(client.request("POST", path+"/decision", decision))
  result = data(client.request("POST", path+"/execute", execution))
  self.assertEqual(result["moved_count"], 2)
  self.assertEqual(data(client.request("POST", path+"/execute", execution)), result)
  ```
- [x] 在受限单元运行新HTTP测试，确认缺失决策/执行接口和等待状态导致RED，不误把夹具错误作为RED。
- [x] schema10新增决定表及等待任务状态，增加版本门槛与迁移无伪造断言。保存时暂停任务；
  决定按修订/摘要核对。事务核心：
  ```cpp
  MySqlTransaction transaction(db);
  // membership -> queue/task -> exclusive scope files -> plan/decision -> policy/targets
  // reject cancelled/unconfirmed/old revision; replay recorded result without another UPDATE
  for (const auto& item : preview["items"])
      db.Execute("UPDATE files SET directory_id=? WHERE id=? AND project_id=?", values);
  // record result + audit + task completed on the same connection
  transaction.Commit();
  ```
- [x] 验证整批成功、相反决定/旧修订拒绝、撤销和冲突、临时触发器中途失败全回滚、
  响应体丢失后重放、并发重复执行、取消前后及服务重启。构建和回归沿用原限额。

### Task 2: 状态展示、审查和发布

**Files:** `resources/js/agent.js`、`test/e2e/agent_ui_test.mjs`、
`test/e2e/m3_agent_browser_test.py`、`docs/api/m3-archive-execution.md`、README、schema链接及证据。
**Interfaces:** GetResult返回kind=archive_operation时正文result=null，archive_operation为最小回执。

- [x] 添加失败DOM测试：waiting状态仍可Cancel；归档完成显示操作回执，不访问answer/citations。
- [x] 补对应原生展示；沿用已有44px控件、纯文本和代次失效保护，无自动模型或写操作。
- [x] 运行前端测试与真实Chrome六屏宽业务检查；静态独立审查与受影响C++回归。
- [x] 更新接口、状态/迁移说明与真实RED/GREEN证据；源文件与被测副本cmp及SHA256一致。
- [x] 通过后核对分支/远程，正常提交推送；保留完整P0目标，下一步接确认网页和MCP编排。
