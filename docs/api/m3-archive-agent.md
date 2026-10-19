# M3 可信归档上下文与提案 Agent

方案 A 第十个增量。复用既有任务、归档策略、MCP 和本机 Agent 入口，
没有新增服务或依赖。仅实现动态搜索/读取后生成方案并退出等待；不自动确认或移动文件。

## 显式选择与持续授权

`POST /api/v1/projects/{project_id}/agent-tasks` 的既有请求可另带
`archive_policy_revision`（1–4294967295整数）。此模式要求 editor/admin，
与 `handover_checklist_revision` 互斥；仍必须确认问题外发并选择1–100个已批准固定版本。
策略必须是指定修订、已批准且有目标。请求不能自带 `archive_context`。

C++保存 `{revision,targets}` 快照。状态接口只增加 `archive_policy_revision`；
`run/start` 才向任务本人返回 `archive_context`：

```json
{
  "revision": 1,
  "targets": [{"directory_id": "32位ID", "name": "已批准名称"}],
  "sources": [{"file_id": "32位ID", "version_id": "32位ID", "source_directory_id": "32位ID"}]
}
```

源目录仅给ID，不附带未批准名称。目标名称是数据，不能充当模型指令或确认。
快照绑定策略修订及名称；重命名、撤销、重新批准不会静默更新已有任务。
工具访问、模型准入及完整方案读取沿用全范围校验，再重验策略。
`run/check` 要求在 `tool_request_ids` 外携带匹配的 `archive_policy_revision`；
缺失、布尔值或错误修订拒绝准入，不增加模型次数。旧任务和普通问答不自动获得归档上下文。
启动响应超过64000字节时原子标记预算失败，不遗留运行任务。

## 执行与等待

归档模式仅提供 `search_documents`、`read_document_version` 和 `create_archive_plan`。
模型可根据结果动态调整检索、读取固定版本，再单独提交方案；不能与其他工具混批。
初次提案只允许 `expected_revision=0` 和绑定策略修订，分类不确定可用null目标，
不得把未解析内容或文件名推断成已核实正文。普通问答仍仅只读，交接任务仍是只读加报告保存。

提案后查询C++权威方案，返回 `waiting_confirmation`（或用户已确认的 `waiting_execution`）
及方案ID、修订、受保护URL；不继续调用模型、不调用finish完成、不声称移动成功。
本机CLI对正常等待返回0，并在脱敏审计中保留等待状态。
网页确认和既有受保护执行接口仍独立；提案循环不提供 `execute_archive_plan`，
更不存在模型确认工具。后续已提供[显式本机继续执行](m3-archive-resume.md)，
不调用模型或自动确认；网页创建归档任务入口仍待完成。

写请求响应丢失/超时后只查询实际状态，不重写方案或抢写失败。
仍无法确认时返回 `unknown`，用户需查询方案/任务；不能据此认定写入已撤销。
如果用户已独立确认并执行，协调查询可以返回真实归档操作回执。
已提交的最小回执在策略失效后仍可按既有成员/本人权限读取，完整方案不绕过授权。

归档预览包含服务端文件/目标名称，工具和权威查询响应有界到256KiB；
完整预览不再发给模型。普通运行响应、单轮模型上下文仍64KiB，累计512KiB，
最多8次模型请求和12次工具调用。归档输出预算为 `min(8192,1024+256*源文件数)`，
普通问答供应商输出上限仍512。100项不是长理由/长上下文一定可运行的保证。

## schema15和部署

`015_agent_archive_context.sql` 仅为任务增加可空JSON快照及约束，既有任务保持NULL，
不虚构选择、批准或执行。新二进制要求schema15，不可直接用于schema14及更早数据库。
正式升级须另行安排停应用、worker/网关，备份数据库和资料，运行 `scripts/migrate.sh`
并确认 `schema_version=15` 后再启动匹配版本。MySQL DDL不是整批事务，失败时按备份恢复，
不要盲目重复部分迁移。本轮只在隔离测试库升级，未迁移或部署原业务服务。

本地脚本模型验证编排而非真实模型分类准确率；没有使用DeepSeek key或付费调用。
验证记录见[证据](../evidence/m3/archive-agent/README.md)。M3及整个P0仍未验收。
