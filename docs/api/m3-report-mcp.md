# M3 报告保存工具与 Agent 接入

在既有只读工具上增加 `save_handover_report`，复用[schema14报告后端](m3-handover-report.md)。
无新数据库迁移、依赖或公共服务。当前实际接通3个工具，归档的2个MCP工具及等待继续编排仍未完成。

## MCP 契约

可信 context 格式、0600文件及 stdio 启动方法沿用[原入口](m3-mcp-read-tools.md)。
`project_approved_versions` 仍仅列出两个只读工具；`task_versions` 增加报告保存工具。
工具参数严格为 `{operation_id,report}`，report 的条目、固定引用、文本限制见后端接口。
整个工具参数 UTF-8 JSON 最多32768字节；客户端不接受身份、项目、任务、批准或URL字段。
C++再次校验清单、引用和全实际来源，Python不复制权限表或自行写数据库。

工具直接向绑定任务的 `PUT /handover-report` 发请求，无代理、重定向、自动写重试或内容缓存。
相同操作ID/内容的显式重试返回原结果；不同内容或权限撤销由C++拒绝。
成功结构仍含request_id、task_id、duration_ms、status；data仅有 `{id,version,url,markdown_url}`。
报告回执不携带 `source_request_id`，不会冒充新读取的来源；URL必须为当前任务固定受保护路径。
注解为readOnlyHint=false、destructiveHint=false、idempotentHint=true、openWorldHint=false。
400/413映射invalid_request，401/403/404为access_denied，409为conflict，429为busy；
超时或传输/畸形回执为timeout/dependency_failed，不返回上游正文。

## 执行器行为

普通问答仍仅向模型提供搜索和读取；成功结果仍是回答草稿。
绑定清单的任务必须从可信 `run/start` 获得合法 `report_operation_id`，否则模型调用前失败。
操作ID和清单分别作为用户数据消息提供；不能把清单内容当系统指令或批准。
此任务向模型提供第三个保存工具，结束必须保存报告，不能用普通回答冒充报告完成。
工具参数中的ID必须等于本次start ID，错误ID在派发前拒绝。

保存必须是该轮唯一工具调用，拒绝保存与读取/其他写操作混批。
每次模型调用前仍累计准入已观察的工具回执并确认清单修订；未送入模型的工具数据不能伪造报告依据。
保存成功后查询C++运行结果，确认报告回执一致便结束，不继续请求模型，也不调用普通answer的finish。
工具拒绝可按原有有界规则纠错；明确权限/范围失败终止。

写入响应丢失、工具传输失败或成功回执畸形时，可能已经提交：只查询权威结果，不自动重写，
不竞争提交failed/finish。结果确认已保存才返回completed；仍running或读取失败则返回unknown，
不把unknown说成失败或成功。后台稍后提交不被覆盖；用户通过既有任务结果接口查看真实状态，
或用同一操作ID和相同内容显式重试。CLI stdout仍只输出脱敏状态，报告经C++受保护接口访问。
外部取消仍向上传播，取消已经抵达后端的请求不能保证撤回已提交结果。

## 输出预算与验证边界

报告的模型输出请求额度为 `min(8192, 1024 + 256 * 清单项数)`，按项数控制而非统一放大；
DeepSeek适配器只在可信工具集合包含保存工具时使用该额度。普通问答保留原512输出token上限。
8次模型、12次工具、180秒总时限、64KiB单次模型消息/响应及512KiB累计发送预算不变。
额度是上限，不代表实际用量或费用；供应商usage缺失仍为未知。超长报告仍可能触发预算/截断，
不会自动提高额度、重试供应商或保存不完整清单。输出额度不能证明模型结论质量。

测试使用本地模型决策、真实MCP SDK/stdio、C++和临时MySQL；供应商适配测试使用HTTPX接收器，
没有真实API调用。网页报告入口、完整模型/MCP审计API、生产内部HTTP和五工具整体验收仍待完成。
验证见[报告MCP证据](../evidence/m3/report-mcp/README.md)。
