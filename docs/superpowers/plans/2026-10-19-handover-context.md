# Handover Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让明确选择的获批交接清单真正进入模型，并对整个任务持续重验修订。
**Architecture:** 复用agent_tasks JSON快照、Scope门禁和run/start，Python只组合可信响应。
**Tech Stack:** 既有C++14/MySQL/Python MCP；无新依赖。
**Spec:** docs/superpowers/specs/2026-10-19-handover-context-design.md

## Global Constraints

schema13；旧任务NULL；最多50清单项；沿用65536字节消息上限，超过失败不截断。
使用现有隔离工作树；重负载串行、make -j1、原资源限额及512MiB停止阈值，无生产迁移/远程调用。

## Task 1: 清单可信上下文全链路

**Files:** code/agent/agent_tasks.cpp、python/smartdocs/agent_runner.py；
db/migrations/013_agent_handover_context.sql、code/app/application.cpp及schema/迁移测试；
新增test/e2e/m3_handover_context_test.py，扩展test/mcp/test_agent_runner.py；文档及证据。
**Interfaces:** Create可选handover_checklist_revision；start可选handover_checklist={revision,items}；
Scope重验快照。失败task_scope_changed，Python映射scope_changed。

- [ ] 写真实端到端失败测试及Python畸形快照测试，先在schema12二进制运行。

```python
task = data(user.request('POST', tasks_path, dict(body, handover_checklist_revision=1)), 201)
started = data(user.request('POST', tasks_path+'/'+task['id']+'/run/start'))
assert started['handover_checklist']['revision'] == 1
```

- [ ] 创建时校验可选精确字段，从handover_checklists读取并插入快照；Scope比对当前修订及批准。

```sql
ALTER TABLE agent_tasks ADD COLUMN handover_context JSON NULL;
SELECT revision,items_json,remote_ai_approved FROM handover_checklists WHERE project_id=? FOR SHARE;
```

- [ ] start返回快照，Python检验结构并作为user JSON数据消息追加；system声明内容无授权效力。

```python
messages.append({'role':'user','content':encoded({'handover_checklist':checklist}).decode('utf-8')})
```

- [ ] 同步schema13断言并串行构建server/tests/admin；新增HTTP+schema/M3分组、既有清单与来源链路、MCP/Python回归。
- [ ] 独立只读审查、保存真实输出与源码指纹、更新README/API/部署要求、正常提交推送。

自审：清单绑定是报告必需前置，不用快照选择冒充模型收件；后续仍需报告保存/下载/页面与5工具编排。
