# M3 Read Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 交付具备持久领取、外发复核、终态与固定出处的有界只读执行核心。

**Architecture:** C++沿用AgentTaskService和原范围检查；Python循环调用已建立的
MCP ClientSession与异步模型适配函数，不另建权限/状态库。

**Tech Stack:** C++14、MySQL8、Python3.10、mcp1.30.0，无新增依赖。

**Spec:** docs/superpowers/specs/2026-10-18-m3-read-agent-design.md

## Global Constraints

- 8模型请求、12工具调用、180秒总限、单模型60秒、max_tokens1024。
- 单轮64KiB、累计512KiB；answer16000 UTF-8字节、citations100项。
- schema7只隔离库升级；模型替身不代表真实模型；不读取密钥。
- 当前服务器、nobody、原slice、-j1、串行900秒；低于512MiB停止。

### Task 1: 持久运行与外发检查

Files: code/agent/agent_tasks.h/cpp、agent_routes.cpp、db/migrations/007_agent_runs.sql、
code/app/application.cpp；已有schema/清库/迁移脚本及processing_jobs_test.cpp。

Interfaces: StartRun(session,project,task)、CheckRun(...)、FinishRun(...,body)、GetResult(...)。

- [ ] 新真实路由测试先断言领取返回question、重复领取task_inactive：
  ```cpp
  const auto run=AgentRequest(app,f,"POST",prefix+"/run/start");
  CHECK(run.at("question")=="检查交接资料");
  CHECK_THROWS_CODE(AgentRequest(app,f,"POST",prefix+"/run/start"),"task_inactive");
  ```
- [ ] 受限构建运行--filter=m3_run_，记录缺失路由RED。
- [ ] 实现服务/路由/迁移；复用范围锁，事务持久领取、预算、结果；更新schema7检查。
- [ ] 构建三个实际二进制，运行新用例及任务/搜索/处理/schema相关完整名称。

### Task 2: 有界只读循环与真实链路

Files: python/smartdocs/agent_runner.py、test/mcp/test_agent_runner.py、
test/e2e/m3_mcp_read_test.py、docs/api/m3-read-agent.md、README.md、docs/evidence/m3/read-agent/。

Interface: async run_task(context, client, model)返回持久状态；model异步返回Chat Completion。

- [ ] 以assertIsNotNone(find_spec("smartdocs.agent_runner"))记录缺失模块断言RED，
  再测模型选择工具、无权/预算/格式错误终止，引用必须来自已返回片段。
- [ ] 实现循环及固定回环C++运行接口适配，不缓存授权、不自动重试模型/领取。
- [ ] E2E模型替身根据实际MCP结果调整查询并读片段，最后校验持久结果；
  在首轮读取后撤销批准，断言下一模型请求未发生、持久failed、无结果。
- [ ] 串行验证MCP套件/Python基线/真实E2E，独立只读审查并修复实质发现。
- [ ] 保存RED/GREEN、SHA、终态及限制，更新README，正常提交推送已有M3分支。
