# Handover Checklist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 持久化管理员维护的项目交接清单，提供带修订和显式外发批准的普通业务API。
**Architecture:** 复用ProjectService及成员→项目锁；一表存当前清单与最近请求。清单与审计同事务，
CAS防覆盖，最近请求精确重放不重复写入。报告及模型上下文消费不在本增量内。
**Tech Stack:** C++14、现有nlohmann JSON/MySQL、Python unittest真实HTTP测试。
**Spec:** docs/superpowers/specs/2026-10-19-handover-checklist-design.md

## Global Constraints

- 现有隔离分支；无新依赖、生产迁移、模型调用或配置框架。
- items最多50，key匹配`[a-z][a-z0-9_]{0,63}`，title为1–200字节且非空白，description为0–1000字节；拒绝C0/DEL。
- PUT最大65536字节、Admin且同源；GET Reader；项目/身份来自认证及URL，不来自JSON。
- expected_revision范围0–4294967294；批准不继承。全请求重放只接受当前等于expected+1且JSON相同。
- 重负载串行、make -j1，全部放入既有smartdocsvectorcheck.slice，MemAvailable低于512MiB即停。

## Task 1: 清单API、schema11和边界验证

**Files:** 新建code/project/handover_checklist.cpp、db/migrations/011_handover_checklists.sql、
test/e2e/m3_handover_checklist_test.py；修改project_service.h、project_routes.cpp、application.cpp、
test/mysql_test_support.cpp、test/integration/mysql_schema_test.cpp、scripts/test-with-mysql.sh、
test/e2e/m2_migration_test.sh；更新README/API/证据文档。

**Interfaces:** ProjectService::HandoverChecklist(const SessionContext&, const std::string&)返回JSON；
SetHandoverChecklist(const SessionContext&, const std::string&, const nlohmann::json&, const std::string& request_id)返回JSON。
复用RequireRole、repository_->InsertAudit、MySqlTransaction和PrepareJsonBody，不新增抽象接口。

- [ ] 写真实HTTP测试，复用TaskListE2E的隔离服务器/项目；独立断言如下，并扩展权限、边界、竞争和回滚：

```python
self.assertEqual(data(self.user.request("GET", path)),
                 {"revision": 0, "items": [], "remote_ai_approved": False})
saved = data(self.user.request("PUT", path, body))
self.assertEqual(saved["revision"], 1)
self.assertEqual(data(self.user.request("PUT", path, body)), saved)
self.assertEqual(count_audit(), 1)
```

- [ ] 在原schema10副本用test-with-mysql.sh运行新测试，确认因404而失败；记录RED。
- [ ] 实现schema11和两条路由。写事务顺序：RequireRole(Admin)→项目FOR UPDATE→读取当前行→
  校验CAS/精确重放→upsert→InsertAudit→Commit。GET在Reader检查后项目FOR SHARE，读取同一快照。
  SQL核心约束为：

```sql
CHECK (revision > 0),
CHECK (JSON_TYPE(items_json)='ARRAY' AND JSON_LENGTH(items_json)<=50),
CHECK ((remote_ai_approved=TRUE AND approved_by IS NOT NULL AND approved_at IS NOT NULL)
    OR (remote_ai_approved=FALSE AND approved_by IS NULL AND approved_at IS NULL))
```

- [ ] 同步副本后受限make -j1构建server/smartdocs_tests/smartdocs-admin；不得并行重型测试。
- [ ] 新临时库运行清单真实HTTP、相关C++，再独立临时库运行既有22项归档HTTP；迁移检查schema11、
  重放、部分schema拒绝及旧文件升级后清单行数为0。命令：

```bash
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_handover_checklist_test.py
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_archive_execution_test.py
bin/smartdocs_tests --filter=mysql_schema
bin/smartdocs_tests --filter=auth_project
bin/smartdocs_tests --filter=authorization
```

- [ ] 独立只读审查；修复发现，保存源码指纹、实际输出和资源清理证据；更新README和部署版本要求。
- [ ] 验证git diff --check、源文件与受限副本一致、所有临时进程退出；正常提交推送现有分支，目标保持活动。

自审：本计划仅一条事务/API链路，没有并行实现任务；schema/路由/测试版本统一11。
清单不自动变成报告，不将普通GET当作模型可用接口；页面和MCP接入保持明确未交付。
