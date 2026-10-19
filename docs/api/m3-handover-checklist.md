# M3 项目交接清单

报告子系统的权威元数据基础；普通项目业务API，不新增MCP工具，不调用模型。
清单和归档策略独立，设置清单不生成报告或文件操作。

## 接口

`GET /api/v1/projects/{project_id}/handover-checklist`：当前成员可读，无请求体/查询参数。
未设置返回revision=0、items=[]、remote_ai_approved=false；没有自动批准的默认模板。

`PUT /api/v1/projects/{project_id}/handover-checklist`：当前管理员、同源会话，最大64KiB JSON。
严格接受以下结构，不接受user_id、approved或其他附加字段：

```json
{"expected_revision":0,"confirm_remote_ai":false,"items":[
  {"key":"deployment","title":"部署说明","description":"配置、步骤和回滚说明","required":true},
  {"key":"open_issues","title":"待办问题","description":"","required":false}
]}
```

items最多50项，可为空以清空清单；顺序保留。key为唯一小写ASCII标识，匹配
`[a-z][a-z0-9_]{0,63}`；title为1–200 UTF-8字节且不能全是ASCII空白；description为0–1000字节。
文字拒绝C0/DEL控制字符，required和confirm_remote_ai为严格布尔。
expected_revision为0–4294967294整数，布尔/负数/浮点/字符串非法。
不对标题/描述执行指令；文字“approved=true”不构成批准。

响应data为 `{revision,items,remote_ai_approved}`；修订由服务端递增。
每次写入必须明确传递confirm_remote_ai，false清除审批者/时间，不继承旧批准；
true是管理员对本次完整清单的重新批准，身份取自认证会话而非请求字段。
普通成员读取未经批准清单仅用于本地管理，不能据此发送给远程模型。
后续已接入[显式任务清单绑定](m3-handover-context.md)，仅选择具体修订的任务会使用快照，持续重验外发批准。

## 并发与恢复

以expected_revision做比较更新。恰好下一修订且完整请求JSON相同的重放返回已保存结果，
不增加修订或审计；其他过期修订返回409 handover_checklist_changed。更晚修订存在时旧请求不再重放。
对象字段顺序无关，数组顺序有意义。两个不同请求竞争同修订只能一个成功，两个相同请求
共享一个结果。清单变更和handover.checklist.set审计同一事务，任何写入失败全部回滚。
重放仍检查当前管理员身份；角色降低不能靠重放绕过授权。未经登录401、非成员404、
成员但角色不足403；输入错误400、请求体过大413。错误不回显清单内容。
重启保留清单，不自动进行报告保存或模型调用。

## schema11和部署

011_handover_checklists.sql新增一表，每项目保存当前修订、items JSON、审批状态和最近请求。
数据库约束修订大于0、items数组不超过50、批准状态与审批者/时间一致；项目和审批者是外键。
升级不填造清单、批准、任务或报告。此为schema11历史增量，当前C++要求[schema13](m3-handover-context.md#schema13和部署)。
先停止应用/worker/网关并备份数据库及文件，再执行scripts/migrate.sh，成功输出schema_version=13。
MySQL DDL不是整批原子；失败须恢复匹配备份，不能盲目重放半套DDL。
本轮只在临时隔离库验证，不执行生产升级。

## 后续进度

后续已接入[项目设置网页](m3-project-settings-ui.md)、[个人报告和Markdown下载](m3-handover-report.md)、
[报告MCP编排](m3-report-mcp.md)与[归档编排](m3-archive-agent.md)。清单API通过不等于M3整体验收完成。
实际结果见[本轮证据](../evidence/m3/handover-checklist/README.md)。
