# M3 Task Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已有只读工具提供C++持久且不可扩大、可取消的本人任务版本范围。

**Architecture:** 新AgentTaskService拥有任务创建/状态/取消/恢复；ProcessingService
在原事务中调用共享范围检查函数，搜索SQL使用持久范围子查询。MCP仅负责认证配置
到固定任务路由的映射。

**Tech Stack:** C++14、MySQL8、Python3.10、已锁定mcp1.30.0，无新增依赖。

**Spec:** docs/superpowers/specs/2026-10-18-m3-task-scope-design.md

## Global Constraints

- 1–100个固定版本；问题最多4000 UTF-8字节；必须confirm_remote_ai=true。
- 最多100个pending/running任务、2个running；单次读取最多10片段。
- 所有任务接口本人+项目reader，POST同源，模型不能提供身份/批准/任务ID。
- 当前服务器、隔离MySQL、nobody、smartdocsvectorcheck.slice、-j1、900秒；低于512MiB停止。
- 不调用模型、不在线迁移、不将持久任务基础冒充完整Agent。

### Task 1: C++持久任务和范围约束

**Files:** 新建code/agent/agent_tasks.h、agent_tasks.cpp、agent_routes.cpp、
db/migrations/006_agent_tasks.sql；修改application.cpp、project_service.h、
processing_service.h/cpp、document_search.cpp、test/mysql_test_support.cpp、
test/integration/processing_jobs_test.cpp、mysql_schema_test.cpp、
scripts/test-with-mysql.sh、test/e2e/m2_migration_test.sh。

**Interfaces:** AgentTaskService(pool,projects).Create(session,project,body)、
Get(session,project,task)、Cancel(session,project,task)返回JSON；RecoverInterrupted()。
RequireAgentTaskScope(db,session,project,task,file="",version="")在调用方事务中执行。
ProcessingService.Search增加最后可选task_id=""，ReadDocument同样增加最后参数。

- [ ] 先在已有真实Fixture写路由用例：
  ```cpp
  auto task=AgentRequest(app,f,"POST","",TaskBody(job));
  CHECK(task.at("state")=="pending");
  auto found=AgentRequest(app,f,"POST","/"+task.at("id").get<std::string>()+"/tools/search",
      {{"mode","metadata"},{"query",""}});
  CHECK(found.at("coverage").at("current_files")==1);
  ```
  同时断言范围外已批准文件不出现、取消后task_inactive、他人task404和非法确认拒绝。
- [ ] 受限make -j1 bin/smartdocs_tests后，隔离库执行--filter=m3_task_，确认route_not_found。
- [ ] 实现schema6和服务/路由；全部范围共享锁+任务行排它锁，SQL搜索追加
  `EXISTS(SELECT 1 FROM agent_task_versions a WHERE a.task_id=? AND a.file_id=f.id AND a.version_id=v.id)`。
  ReadDocument在正文查询前调用共享函数；任何范围失效不给数据。
- [ ] 更新schema检查、重放测试、清库表；构建测试与两个实际二进制，分组运行所有C++完整名称。

### Task 2: MCP持久任务绑定与真实链路

**Files:** python/smartdocs/mcp_read.py、test/mcp/test_mcp_read.py、
test/e2e/m3_mcp_read_test.py、docs/api/m3-agent-tasks.md、m3-mcp-read-tools.md、README.md、
docs/evidence/m3/task-scope/。

**Interfaces:** context.scope允许task_versions；server task_id仅来自可信配置。
两个工具参数不变；backend_read在task模式将search/read都POST到/tasks工具路径。

- [ ] 协议RED：context.scope=task_versions时，真实SDK调用search后检查唯一HTTP请求
  为POST `/api/v1/projects/{project}/agent-tasks/{task}/tools/search`，不走旧全项目路径。
- [ ] 增加分支构造固定路由并传原工具参数，不允许HTTP失败后降级到旧接口。
- [ ] 真实E2E创建任务，scope=task_versions：搜索只见选中文档，读取另一已批准文档拒绝，
  取消任务后相同stdio客户端再次读取拒绝；重启服务器后任务interrupted。
- [ ] 串行跑MCP全套、Python基线、真实E2E；只读独立审查，修复实质问题并复验。
- [ ] 写明schema6升级要求/未部署/剩余Agent门槛，保存原始日志/源码SHA/终态；
  git diff --cached --check，确认remote和branch，正常提交推送，不合并或删除工作区。
