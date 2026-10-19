# Handover Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 个人报告幂等保存、完整实际来源鉴权与Markdown导出。
**Architecture:** 复用C++任务锁/范围门禁与来源回执；独立报告实现文件、一表、运行操作ID。
**Tech Stack:** 既有C++14/MySQL，无新依赖。
**Spec:** docs/superpowers/specs/2026-10-19-handover-report-design.md

## Global Constraints

schema14；每任务一个version1草稿；body65536字节；最多50清单项/总100引用；操作ID来自start。
重负载串行、当前ECS原限额与512MiB停止阈值；无生产迁移/远程模型调用。

## Task 1: 报告持久闭环

**Files:** 新增code/agent/handover_reports.cpp及db/migrations/014_handover_reports.sql；
修改agent_tasks.{h,cpp}/agent_routes.cpp、HttpResponse文本输出及schema断言；
新增test/e2e/m3_handover_report_test.py；更新API/README/部署与证据。
**Interfaces:** SaveHandoverReport(session,project,task,body,request_id)、GetHandoverReport(session,project,task)。
ReadHandoverReport(db,project,task)返回完整报告或null，调用者必须持有成员/本人任务锁。
RequireReportDependencies(db,project,sources,checklist)复用已有Allowed/Checklist门禁。
RenderHandoverMarkdown(report)返回安全文本；HttpResponse::Text(status,body,headers,keep_alive)。

- [x] 写真实HTTP/SQL失败测试，验证旧版无operation ID/报告路由，保留RED。

```python
started = data(user.request('POST', path+'/run/start'))
receipt = data(user.request('PUT', path+'/handover-report', {'operation_id':started['report_operation_id'], 'report':report}))
assert data(user.request('PUT', path+'/handover-report', body)) == receipt
```

- [x] 实现迁移、start操作ID、报告校验和同事务保存；来源取已准入回执并验证片段存在。

```sql
SELECT source_json FROM agent_tool_calls WHERE task_id=? AND model_call IS NOT NULL ORDER BY sequence FOR SHARE;
INSERT INTO handover_reports(id,task_id,operation_id,request_json,content_json,sources_json,checklist_json) VALUES (?,?,?,?,?,?,?);
UPDATE agent_tasks SET state='completed' WHERE id=?;
```

- [x] 实现受保护GET、Markdown和run/result分类；所有全文出口重验实际依赖和清单，不读旧缓存。
- [x] 同步schema14、受限构建及真实HTTP/相关C++、清单上下文和来源回归，检查资源终态。
- [x] 独立只读审查并修复/复验，记录源码指纹和真实输出、更新文档。

提交发布：本实现随当前分支正常提交推送；最终结果以Git远端核对为准。

自审：这是报告后端实际交付，不把引用格式当语义准确率；UI与MCP生产编排仍需随后完成。
