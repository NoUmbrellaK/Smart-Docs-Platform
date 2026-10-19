# M3 模型请求来源回执

这是报告依赖的前置能力，不是已保存的个人报告；没有新增MCP工具或模型调用入口。
源数据由C++工具响应建立，不由模型引用列表或文档文字建立。

## 请求准入

`POST /api/v1/projects/{project_id}/agent-tasks/{task_id}/run/check`
从schema12起必须带JSON（最大4096字节，无查询参数）：

```json
{"tool_request_ids":[]}
```

数组最多64个互异的小写hex32后端request_id，无额外字段。首次无工具历史时传空数组。
已有来源时，每次传累计集合，含所有之前准入回执；后续不能删除历史依赖。旧的无body请求拒绝。
只接受本任务实际记录且状态为success/processing/unavailable、source_json非NULL的回执。
跨任务、未知、错误、unknown或schema12之前没有来源记录的回执均400 invalid_source_receipt，
错误不回显输入。普通Reader可执行本人任务；仍检查同源、当前成员、整个任务范围、取消和180秒截止期。
有效准入仍返回`{model_calls:N}`，最多8次；先完整校验，再将新回执的首次model_call与计数同事务保存。
故障、伪造或删历史不部分更新、不消耗本次模型调用次数。预算/范围已失效时仍按原规则终止任务。
同一个check重发是新一次模型尝试，不提供模型调用幂等或自动重试。

## 来源与MCP链路

agent_tool_calls.source_json保存`{versions:[{file_id,version_id}],citations:[{file_id,version_id,chunk_id}]}`，
去重排序。仅扫描真实响应的data、items、chunk、chunks已知位置，不递归解析正文里的JSON。
即使只有文件元数据、处理中或不可解析状态，也记录该文件版本；错误响应不产生来源。
来源与工具结束状态同次UPDATE，失败不返回原文。来源不含文件名、问题或正文，普通审计接口不返回新字段。

MCP任务模式从后端HTTP envelope提取request_id，置于顶层source_request_id，业务data不改写。
Hybrid预检查结果丢弃，只有最终再次鉴权的响应回执会返回。project_approved_versions调试模式无来源回执，
不能用于持久Agent执行；工具参数不接受source_request_id、tool_request_ids或用户身份。
可信执行器逐个收集实际加入消息历史的回执，下一次模型调用前提交check；有data而缺有效回执则失败关闭。
source_request_id不进入模型消息，普通MCP调用request_id仍保留用于脱敏摘要。

账本表示“获准尝试发送的上下文”，不是供应商收件证明。请求可能未发出或超时但已经送达，
安全上保留这类依赖；不能据此统计真实供应商成功率。当前不复用跨任务会话。

## schema12和部署

012_agent_tool_sources.sql仅给agent_tool_calls增加source_json和model_call及约束。
旧行NULL保持未知，不从参数、引用、全任务范围反推或伪造来源。
当前二进制要求schema12；停应用/worker/网关，备份数据库及文件后迁移，成功输出schema_version=12。
C++、MCP、执行器必须同步更新，旧客户端的无body check和缺回执响应不兼容。
MySQL DDL不是整批原子事务；中途失败应恢复匹配备份，不盲目重放；本轮只迁移隔离库。

## 尚未交付

个人报告存储/引用校验/Markdown下载、清单与目录上下文批准绑定、第五工具和完整写工具编排仍待实现。
后续报告必须使用已准入来源的全集，包含未被显式引用的版本，并在保存/展示/下载重新校验；
当前普通回答草稿仍按整个任务范围重验，不能将它当作报告。固定引用存在也不证明原文支持结论。
实际验证见[本轮证据](../evidence/m3/model-sources/README.md)。
