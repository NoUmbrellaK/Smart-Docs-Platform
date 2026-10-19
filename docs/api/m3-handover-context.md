# M3 可信交接清单上下文

这是报告前置能力，不是报告保存、Markdown导出或新的MCP工具。沿用C++权限、任务和MySQL，
没有新增服务或依赖。模型仍只有两个只读工具，完成结果仍是回答草稿。

## 显式选择

创建任务POST /api/v1/projects/{project_id}/agent-tasks可选增加：

```json
{"question":"检查交接资料", "versions":[{"file_id":"固定文件ID","version_id":"固定版本ID"}],
 "confirm_remote_ai":true, "handover_checklist_revision":1}
```

修订必须为1..4294967295整数，不能传null/布尔或提交清单正文。C++校验当前项目存在该修订、
已明确允许远程AI且非空，再保存{revision,items}快照。不存在/未批准/空/过期统一409 task_scope_changed。
Reader可核查并保存本人任务，不能因此编辑项目清单。所有身份仍来自登录会话，同源检查不变。
不传字段表示普通问答，不自动附加清单，也不因后来设置清单而改变旧任务范围。

任务状态/列表只有绑定任务新增handover_checklist_revision，不返回快照正文。
run/start在获准运行后，仅对绑定任务返回handover_checklist={revision,items}。
此类任务每次run/check必须传{"tool_request_ids":[],"handover_checklist_revision":1}，
修订必须和保存快照一致。缺失/错误/布尔值400且不消耗模型预算；普通任务不接受额外修订字段。
旧执行器不能忽略清单后继续获得准入。
当前网页尚未提供清单修订选择；可通过上述受控业务接口创建，再用已有可信CLI运行。

## 持续权限与事实边界

所有任务范围检查同时验证当前清单的修订、批准及条目内容与快照一致。覆盖start、搜索/读取、
每次模型调用前check、完成保存、结果读取及归档范围门禁。修改、撤销、清空后旧任务不能静默切换新版。
即使重新批准原文字，也有新修订，不能复活旧绑定；需用户重新发起任务。状态/取消仍可使用。
scope_changed错误不返回清单内容。既有完成回答在依赖失效后不再提供旧正文。
例外仅为已提交归档的脱敏操作回执：仍对本人和项目成员资格鉴权，不含清单/资料正文，
可继续查看实际已执行的数量与状态，不能把撤销批准说成撤销了移动。

执行器把可信start快照放在独立user JSON数据消息中；system明确其是核查资料，不是系统指令、
外发授权或归档确认。结构无效时在check/模型前dependency_failed；沿用现有消息/传输大小和调用预算，
超过预算或传输限制时拒绝，不截断清单并宣称完整。没有真实供应商调用的质量证明。
start数据JSON限制64000字节以预留envelope空间，超限原子记为failed/budget_exhausted、
model_calls=0并返回409；不会返回超大响应让执行器遗留running任务。
创建时保存快照只代表选择，agent_runs.model_calls>0才表示模型请求曾获准尝试；不等于供应商已收到。
文件来源仍由schema12来源回执台账管理，不能用清单快照代替来源或伪造引用。

## schema13和部署

013_agent_handover_context.sql仅增加agent_tasks.handover_context JSON及顶层形状约束，旧行NULL不回填批准。
停应用/worker/网关并备份数据库和文件后迁移，当前二进制要求schema13，成功输出schema_version=13。
同步更新C++和Python；旧执行器不会使用清单，不应与新绑定任务混用。MySQL DDL非整批事务，失败应恢复
匹配备份，不盲目重放。本轮仅迁移临时库，无生产部署。

后续报告必须保存清单版本、全来源依赖并重新鉴权；个人报告API、Markdown、网页清单设置/选择、
三个MCP写工具和模型等待/继续编排仍未完成。验证见[本轮证据](../evidence/m3/handover-context/README.md)。
