# Model Source Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持久绑定真实工具输出和模型请求上下文，保留未被模型引用的来源。
**Architecture:** 在既有工具审计表保存C++生成的来源，check事务准入回执；MCP传递最终HTTP回执，执行器累计。
**Tech Stack:** 既有C++14/MySQL、Python MCP SDK/unittest，无新增依赖。
**Spec:** docs/superpowers/specs/2026-10-19-model-source-ledger-design.md

## Global Constraints

- schema12，无生产迁移/模型调用；只存ID，禁止正文日志及扫描文档指令。
- check精确tool_request_ids数组，最多64个互异hex32，4096字节；模型预算仍8。
- 复用现有隔离工作树，重负载串行、make -j1及原切片限额，MemAvailable<512MiB停自有单元。

## Task 1: 完整来源回执链路

**Files:** 修改code/agent/agent_tasks.{h,cpp}、agent_routes.cpp；新增code/agent/tool_sources.{h,cpp}；
迁移012_agent_tool_sources.sql、application.cpp/schema与迁移测试；python/smartdocs/mcp_read.py和agent_runner.py；
新增test/e2e/m3_model_sources_test.py，扩展test/mcp/test_agent_runner.py和test_mcp_read.py；更新版本/API/README/证据。

**Interfaces:** ExtractToolSources(const nlohmann::json&)返回{versions,citations}；
FinishToolCall(request,status,duration,data)存来源；CheckRun(session,project,task,body)事务准入回执。
MCP任务响应顶层source_request_id；runner的check body为累计tool_request_ids。

- [x] 写失败测试并运行既有schema11副本与Python单测，确认缺失来源协议RED。

```python
response = self.user.request('POST', path+'/tools/search', {'mode':'metadata','query':''})
request_id = response.json()['request_id']
self.assertEqual(self.user.request('POST', path+'/run/check', {'tool_request_ids':[request_id]}).status, 200)
self.assertEqual(self.count("SELECT COUNT(*) FROM agent_tool_calls WHERE model_call=1"), 1)
```

- [x] C++从已知响应位置去重ID，source_json与status一起写；check验证完整回执后标记首次model_call。

```sql
UPDATE agent_tool_calls SET model_call=COALESCE(model_call,?) WHERE task_id=? AND request_id=?;
UPDATE agent_runs SET model_calls=model_calls+1 WHERE task_id=?;
```

- [x] MCP只转发最终响应回执；runner收到data必须验证回执，累计后check再调用模型。

```python
await backend.request('check', {'tool_request_ids': sorted(receipts)})
```

- [x] schema12同步版本断言，改旧C++测试check调用显式空数组；受限构建3个二进制。
- [x] 串行运行新增HTTP/SQL和相关C++，独立库运行真实SDK/执行器链路；运行MCP和既有Python单测。
- [x] 独立只读审查，修复并复验；保存实际输出/源码指纹/资源检查，更新文档；按用户授权正常提交推送。

自审：一条跨层来源链，不能用Python模拟响应代替真实C++来源记录；报告消费尚未交付且明确记载。
