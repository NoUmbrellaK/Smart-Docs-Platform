# 方案A：任务交接清单上下文

基线55b3bee；需求A-03、7.1、9.2。报告来源台账已存在，但任务尚未绑定实际清单。
本增量完成可信清单到模型的链路，不声称报告保存/Markdown或第五工具已经交付。
沿用用户批准的方案A与自主推进授权，不重复确认小范围实现。

## 选择

自动给所有任务加当前清单会扩大普通问答外发范围；让模型提交清单正文无法证明批准。
采用创建任务时可选handover_checklist_revision，用户选择具体修订，C++读取并保存批准快照。
不传则保持普通问答，既有任务不回填。没有新的MCP工具、服务或依赖。

## 协议与存储

schema13：agent_tasks增加handover_context JSON NULL，非NULL为{revision,items}，revision为正整数，
items为1..50项。旧任务NULL。快照只能在创建时由C++从本项目获批清单复制，不接受客户端正文。
创建体仅可额外带handover_checklist_revision，正整数<=4294967295；清单不存在、空、未批准或修订不符409。
状态/列表仅对绑定任务增加handover_checklist_revision，不返回快照正文。
run/start在既有输出增加handover_checklist快照，仅绑定任务有该键。创建不等同模型已读取；
只有后续check增加model_calls后才是获准发送，与文件台账相同保守语义。
绑定任务check必须额外带与快照一致的handover_checklist_revision，否则400且不扣预算；
普通任务仍只接受tool_request_ids。阻止旧执行器忽略start快照后继续调用模型。
start数据JSON超过64000字节时在同一事务持久failed/budget_exhausted/model_calls=0并返回409，
预留HTTP envelope空间，不返回超大内容也不遗留running任务。

所有Scope调用重验绑定清单的当前修订、批准和完整items；不切换最新版。
覆盖start、工具搜索/读取、check、finish、结果及归档范围检查。更新/撤销/清空使旧绑定失效，
错误复用task_scope_changed，模型入口按scope_changed终止。修改再恢复也不能复活旧修订。
普通任务不依赖清单，不受清单变化影响；状态/取消不返回敏感快照，仍可操作。
已提交归档的脱敏回执（操作ID/数量/状态，无资料或清单正文）沿用原有可查询规则；
不因范围撤销隐藏已发生的移动。归档预览和新执行仍重验完整范围。

Python仅接收可信start字段，以独立user数据消息JSON加入上下文；system明确清单是资料而非权限或指令。
没有快照的旧任务不注入；畸形快照在调用模型前dependency_failed。
沿用总消息65536字节/累计524288字节/8次调用预算，大清单超过预算明确终止，不截断冒充完整。
模型完成仍只是回答草稿。后续报告使用此快照并在保存/展示/下载重验，不能将选择状态冒充已准入。

## 验证与部署

真实HTTP/SQL：缺失/未批准/过期/空清单拒绝；Reader可创建本人绑定；正文不可伪造；
重启快照保持；每个出口在修改后拒绝；普通问答不受影响；真实SDK+本地模型接收器验证清单进入
第一请求且撤销后无下一次请求。schema约束与旧行NULL检查。无付费供应商调用。
当前ECS单元串行，CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、双向5MB/s和200IOPS；
MemAvailable<512MiB停止自有单元。只迁移临时库，部署须备份并同步C++/Python到schema13。
