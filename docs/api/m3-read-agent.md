# 有界只读Agent执行核心

本接口为可调用的Python库和C++运行API，不是生产Agent入口。后续已增加
[DeepSeek适配器与可信本机CLI](m3-deepseek.md)，但尚无真实模型效果验证、
内部Streamable HTTP接线；已有[任务网页](m3-agent-workspace.md)创建pending/查看/取消，
但不自动调用执行核心。只开放两个已有只读MCP工具。
调用方必须是可信服务端，model参数是异步函数，不是模型提供的代码或地址。

## 运行API

前缀`/api/v1/projects/{project_id}/agent-tasks/{task_id}/run`，复用本人任务、
有效会话、项目reader权限；POST同源；拒绝query，start/result拒绝正文，check/finish要求JSON。

| 路径 | 行为 |
| --- | --- |
| POST /start | 仅pending可领取；检查全范围、运行容量，持久running及唯一run；返回state/question。question来自用户此前确认的数据库记录，不能覆盖 |
| POST /check | 严格JSON `tool_request_ids`累计数组；仅已领取running任务，复核全范围和180秒期限，最多8次；来源回执和model_calls同事务准入，见[来源协议](m3-model-sources.md) |
| POST /finish | 严格JSON `status/result/error_code`，保存completed回答或failed原因；返回state |
| GET /result | 返回state、model_calls、result、error_code；已完成回答再次核验整个任务依赖，失败/取消/中断不返回正文 |

start/check不幂等，不自动重试。许可只代表本次外发前的事务检查，不是长期授权：
许可提交后并发撤销无法撤回已开始请求，不能宣称数据库事务与网络发送原子化。
后续请求必须重新复核，不跨任务复用上下文。领取响应不明确时查询状态，不再领取。
单个任务只运行一次，失败后新建任务，不把旧执行自动续接到新的任务。

finish成功示例（最多min(65536,全局JSON限额)字节）：

```json
{
  "status":"completed",
  "result":{"answer":"根据已读取资料……","citations":[
    {"file_id":"32位小写十六进制","version_id":"32位小写十六进制","chunk_id":"64位小写十六进制"}
  ]},
  "error_code":null
}
```

answer非空且最多16000 UTF-8字节；citations最多100个不重复片段。
C++验证引用属于持久任务范围且片段存在；Python另外要求来自本次实际工具输出。
这是来源有效性校验，不证明语义正确；无证据回答可用空引用明确说明不足。
完成前再次检查范围和期限，至少有一次已获准的模型请求。迟到结果不能覆盖取消、
中断或失败状态。相同终态和结果重放返回既有状态；不同结果409，不创建新业务对象。
该回答是任务草稿，不是M4交接报告，不提供归档能力。

以上为只读模型回答路径；schema10新增的[归档执行](m3-archive-execution.md)使用独立确认和事务接口。
其完成结果由同一GET run/result返回kind=archive_operation、result=null和最小archive_operation回执，
客户端须按kind区分，不能当answer/citations读取。该内容无关回执不要求旧来源继续获外发批准。
waiting_confirmation/waiting_execution拒绝本页模型start/check/finish，不自动恢复模型执行。

失败时status=failed、result=null；error_code仅可为timeout、budget_exhausted、
model_invalid、invalid_source、dependency_failed、scope_changed、repeated_tool_error、
access_denied或cancelled，不接受供应商错误正文。模型检查/结果查询观察到范围失效时
持久failed并清除旧结果，重新批准也不恢复；只按当前业务状态检查，不监听全部
历史策略变更事件。权限已被完全撤销时接口拒绝，不能冒充有权写失败记录。

## Python入口与限制

`await smartdocs.agent_runner.run_task(context, client, model)`：context必须为已有
`task_versions`格式；client是绑定相同任务的MCP ClientSession；model为异步
`(messages, tools, max_tokens, timeout) -> Chat Completion dict`。
适配器由服务端代码决定，不从用户/模型输入加载Python代码、URL或凭据。
仅保留assistant content/tool_calls供后续上下文使用，不转发隐藏推理与未知字段。

8次模型、12次工具、180秒执行时限，单次模型最多60秒并请求max_tokens=1024；
每轮messages/tools序列化最多64KiB，累计最多512KiB。固定工具schema拒绝身份/
批准/任意工具；错误可在限额内调整，相同失败工具重复即停止。来源失效、无权、
依赖故障等立即终止并清空本地上下文。字节和次数是保守请求限额，不是精确token
或价格估算；DeepSeek适配器另外实现网络截止期、响应上限、usage记录，
并将每次输出请求收紧至512token；自定义适配器仍须实现这些传输约束。

结果只在C++确认保存后返回completed。完成响应不明确时最多重放一次完全相同的
幂等finish，再查询权威结果，绝不提交相反终态。协调各等待最多15秒，最多额外30秒；
此时不再调用模型/工具。查询失败或仍为running时返回unknown，不虚构成功或失败。
首次完成写入被权威接口明确拒绝invalid_source时，则提交failed/null，不把明确拒绝误作网络不确定。
HTTP线程可能晚于等待超时才结束，取消等待不代表撤销已发请求；C++终态锁防止覆盖。
Python协程被强制取消时不续跑；进程消失
但C++未重启时running可由用户取消，C++重启会标记interrupted。无自动故障恢复。
数据库持久化model_calls及[后端工具请求轨迹](m3-tool-audit.md)；可信CLI保留本地脱敏
工具/模型事件及usage记录，尚未接入可查询的完整模型/MCP任务审计API。

## schema7与验证边界

schema7新增agent_runs，扩展agent_tasks状态，不回填虚构运行；当前C++已要求
[schema13](m3-handover-context.md#schema13和部署)，不可用旧schema12及更早版本直接启动。
升级须停C++/worker/网关并备份后运行scripts/migrate.sh；MySQL DDL非整批原子事务，
中途失败恢复备份，不盲目重放；回退需恢复匹配版本备份。只验证隔离测试库，
原业务库未升级，不要直接替换旧库上的二进制。

执行器测试的供应商是本地脚本化替身；其动态分支证明循环会使用工具结果，不证明
真实模型能正确理解资料，也不作为T-10/T-18完整验收或准确率成绩。后续真实供应商
验证只用公开模拟资料、小额预算，并核对模型标识、数据政策与费用。
