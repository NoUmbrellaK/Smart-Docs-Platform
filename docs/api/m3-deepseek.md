# 受控 DeepSeek 传输与本机入口

沿用[只读执行核心](m3-read-agent.md)和C++权威任务范围；不是匿名公网服务。
本页的初始增量不新增依赖、数据库迁移或自动恢复；后续已接入[报告保存工具](m3-report-mcp.md)。调用方仍须先由用户明确确认
具体版本范围，创建本人任务并生成受保护MCP context；不得替用户伪造业务批准。

## 固定出口与预算

`smartdocs.deepseek.DeepSeekModel`是执行核心所需的异步模型适配器。固定请求
`https://api.deepseek.com/chat/completions`，模型`deepseek-flash`，非思考模式、
temperature=0、非SSE；使用已锁定httpx0.28.1，不引入供应商SDK。
HTTPS证书验证开启，trust_env=False，不读取代理/自定义CA环境，不跟随重定向、
不自动重试、不切换供应商。传输参数仅供可信服务端代码使用，不接受模型提供的URL。

每个任务独立实例、最多8次请求，拒绝同实例并发；完整请求正文≤64KiB，
响应原始字节≤64KiB，要求identity编码，按8KiB读取；单次完整异步请求截止期≤60秒，
普通问答输出最多请求512token；绑定清单报告按项数请求 `min(8192,1024+256*项数)`，由执行器提供，适配器核对可信工具集合。
额度、usage和请求字节仍记录于审计；报告额度并不保证任意长清单都能生成完整内容。异步取消会关闭客户端/响应；已经
抵达供应商的请求仍可能产生费用，不能宣称取消即撤回。领取、工具、全任务时限与
外发前依赖复核仍由既有核心执行。字节/次数限额不是精确货币预算。

普通问答请求显式设置 `response_format={"type":"json_object"}`；
仅在该模式的系统提示后追加裸JSON要求和不足证据的格式示例。
依据[官方JSON输出文档](https://api-docs.deepseek.com/guides/json_mode/)，
示例不提供虚构引用ID，有证据时仍须使用实际工具返回的固定版本出处。
报告/归档任务终结于写工具，保留原提示、请求格式和额度，不增加此参数。
工具定义与后续选择仍由原执行器处理，不强制固定搜索顺序。

该参数约束供应商输出格式，不代替本地严格解析、去重键/非有限数拒绝、
回答字段和实际引用校验。空内容、非法JSON、截断及伪造引用仍失败，
不剥除围栏、不修补回答、不增加重试或输出预算。
2026-10-20已通过离线请求接线及回归，尚未验证该配置与真实工具调用组合的效果；
不能将此前两次真实失败改记为通过。

401/402/429/5xx分别返回provider_auth、provider_balance、provider_rate_limit、provider_unavailable，
其他非200（含重定向）返回provider_rejected。仅从HTTP状态映射固定错误码，不读取或传播
供应商错误正文；同一码保存在受控轨迹和C++任务失败结果，现有网页Reason字段可查看。
鉴权/余额问题需管理员检查配置或账户，限流/不可用应先确认持久任务状态、稍后由用户
显式新建任务；原失败任务不自动重跑，也不跟随重定向或切换供应商。响应未包含有效usage时
仍为null，不据此声称没有计费。网络传输和审计等其他故障仍可能为dependency_failed。
超时返回timeout，超次数/请求大小返回budget_exhausted，非法模型结果终止。
隐藏推理与未知响应字段不返回给核心；无第三方追踪。

此错误码扩展不修改schema16，也不重写历史dependency_failed记录。部署时须配套更新
C++和Python；旧C++不接受新增finish错误码，可能留下running任务，不能仅替换适配器。
本轮仅在隔离环境验证，未部署生产服务。

MCP 明确返回 `timeout` 时也保留该原因，不合并成 `dependency_failed`：搜索/读取终止为
`failed/timeout`；报告保存或方案创建超时后仅查询权威结果。若已有提交回执，返回真实的
报告完成或方案等待确认；若尚无法确认，返回 `unknown/timeout`，不重写、不抢写失败状态。
归档继续执行沿用同样的未知结果规则。超时不证明服务端没有提交，也不证明没有计费。
这项修复只更新 Python，无新增错误码、数据库迁移或 C++ 重建；历史失败原因不猜测回填。

## 本机可信入口

```bash
PYTHONPATH=python /path/to/mcp-venv/bin/python -B -m smartdocs.agent_cli \
  --context /private/task-context.json --audit /private/new-task-trace.jsonl \
  --max-model-calls 6
```

如需本地hybrid查询，可另加`--encoder-socket /run/smartdocs-encoder/encoder.sock`。
该可信配置只传给MCP子进程，模型不能选择路径；服务须事先READY，详见
[MCP查询边界](m3-mcp-read-tools.md#本地hybrid查询边界)。省略或不可用时明确关键词降级，
不会改用其他远程向量供应商。

context沿用[已有格式](m3-mcp-read-tools.md)，必须task_versions、当前运行用户所有、
普通文件且精确0600，拒绝符号链接。audit必须尚不存在，父目录应受保护；
文件独占创建0600，每条记录flush+fsync；拒绝覆盖与符号链接。不要把这些文件放进Git。
从服务端环境读取DEEPSEEK_API_KEY，随后从环境移除；不接受命令行密钥。
例如用服务管理器的EnvironmentFile指向已授权的仓库外0600配置文件，**不要cat或打印文件**。
MCP子进程只获得明确的运行配置，不获得该密钥；context中的用户会话也不进入模型正文。
stdio任务调试范围可枚举五工具；普通问答模型仅两个只读工具，清单模式另有报告保存，
归档提案模式另有方案保存，模型均不获得确认或执行权限。
stdout只输出简短任务状态，不输出回答或原文；回答和报告经C++结果接口重新鉴权读取。

已有真实确认的归档可用[独立 `--resume-archive` 模式](m3-archive-resume.md)显式继续：
不需要密钥、不调用模型、不自动确认或重试写入，仍需可信任务context及新的审计路径。

已有 pending 任务也可使用[受控同源 HTTP 入口](m3-agent-http.md)，由当前会话临时
绑定 context 并复用同一执行器；不增加自动队列、不接收模型参数，需独立部署。

## 受控轨迹

每条绑定task_id及时间。model_attempt在网络发送前落盘，包含请求关联ID、模型、
序号、字节数、输出限额；model_result记录耗时、脱敏原因和经校验的usage计数。
缺失/非法usage为null，不当作0token或免费；连接中断时计费未知。
工具调用前持久写attempt，异常/取消也记录result；工具摘要只记录名称、耗时、状态、返回条目/片段数量及查询是否改变，不记录查询、
正文、文件名、会话、密钥或思维过程。task_outcome只记录经白名单校验的状态/错误码。
模型响应成功不等于业务任务完成；进程在attempt后消失表示结果未知，不自动续跑。

### 模型结果拒绝诊断

在受控CLI/HTTP执行路径中，执行器产生 `model_invalid`、且尚未尝试保存报告/提案/完成结果时，
私有轨迹可增加一条 `model_rejection`。它不新增数据库错误码、不改变普通网页/CLI返回，
也不放宽JSON、引用或工具校验；不保存原始响应、工具参数、ID、异常文本或隐藏推理。

| 字段 | 含义 |
| --- | --- |
| `sequence` | 本次运行的模型调用序号；汇总时必须匹配最后一个已有result的模型attempt |
| `stage` | 执行器最后进入的校验阶段，不等于已证明的模型根因 |
| `finish_reason` | 仅stop/tool_calls/length/content_filter；缺失或null为missing，其余值为other |
| `content_kind` | absent/null/string/other，仅正文形状，不记录长度或正文 |
| `tool_calls_kind` | absent/null/list/other |
| `tool_call_count` | 非list为null；list计数封顶5，5表示至少5项，合法批次最多4项 |

阶段固定为：model_call、response_size、choices_shape、message_shape、content_shape、
final_mode、final_json、final_shape、tool_batch、tool_call_shape、tool_call_id、
tool_arguments_shape、mixed_write_batch、internal_error。
例如final_json表示最终正文JSON解析阶段异常，不自动判定是Markdown围栏；
tool_batch需结合finish_reason与形状判断，length不等于授权提高输出预算。
model_call意味着适配器未成功交回响应；internal_error是未进一步细分的阶段，
不能将其描述为已确认的响应格式问题。

未知内容仅归类，不回显；诊断落盘失败收敛为dependency_failed，不重试模型。
写入已尝试或完成请求已构造时仍只走原有回执协调，不为追加诊断改变不确定写入处理。
旧轨迹缺少该事件时不猜测回填；本增量无法恢复2026-10-20真实失败的原始回答。

这是受控本地运行证据，尚不是数据库可查询、可分页的完整模型/MCP任务审计API；
C++现提供[后端请求轨迹](m3-tool-audit.md)，不能等同本地模型usage或MCP耗时。
平台完整调用摘要、生产内部HTTP认证和审计保留策略仍待后续接入。

### 本地只读用量汇总

服务管理员可在MCP虚拟环境中对一个已停止写入、由当前账号拥有的0600普通轨迹文件执行：

```sh
PYTHONPATH=python /path/to/mcp-venv/bin/python -B -m smartdocs.audit_summary /private/task-trace.jsonl
```

命令不读取API key、不调用模型/数据库、不修改原文件；不提供网页或匿名HTTP入口。
仍由操作系统权限控制原始轨迹访问，父目录必须受保护。单次最多256KiB、128条记录，
拒绝符号链接、FIFO/目录、非本账号文件、非0600文件、重复JSON键、跨任务混合、
重复结果、孤立结果和不匹配的请求ID/工具/序号。不支持把多个运行/归档继续轨迹拼成一份。
当前格式只接受既有固定模型标识；模型/日志格式变化需更新校验及记录版本。

输出JSON的含义：

- `model.attempts/results/unresolved`：本日志记录的尝试、结果和缺失结果数。
  attempt在网络发送前落盘，因此不等于实际到达供应商的请求或计费次数。
- `model.usage_observed`：有效usage所覆盖调用的计数之和，是已知部分，不是缺失部分为零。
  `usage_known_calls/usage_unknown_calls`显式给出覆盖分母；未知包含未得到结果和无效usage。
  `usage_total`仅在日志完整且全部调用usage有效时给出，否则为null。
  缓存命中/未命中计数仅在所有已知usage均有有效缓存明细时汇总，否则不输出缓存字段。
- `model.duration_ms_observed`与`tools.<name>.duration_ms_observed`分开累计已记录结果的耗时；
  不把模型生成时间当工具延迟，也不估计缺失结果耗时或声称是整个任务墙钟耗时。
- `trace_complete`只说明有起始/终态且尝试与结果配对、没有截断尾行；
  `outcome`是经白名单过滤的历史日志状态，可能已过时，不替代C++当前状态。
  `business_acceptance`始终为not_evaluated，不能用completed推导语义评测通过。
  结构校验不提供日志防篡改证明，也不能发现被完整删除的调用或替代供应商账单核对。
- 缺失终态、未结束调用或无换行的最后残片仍返回已知观察，但trace_complete=false。
  完整行若损坏则整份拒绝；不静默丢掉中间错误再计算成功率。运行中日志只应视为快照，
  正式记录应先确认写入进程已退出，本命令不锁定或停止写入者。
- `cost_estimate`始终为null：未自动获取当前价格、峰谷档或供应商账单，不把未知用量算成免费。
  需结合执行时核实的价目、缓存覆盖与供应商账单另算，缺失数据标不可得。
- `model_rejection`为通过白名单及序号校验的私有诊断，旧轨迹或未记录时为null。
  拒绝重复、孤立、错误序号、未知字段/枚举、非法计数，以及诊断后继续工具/模型调用或
  声称completed的矛盾轨迹。没有终态仍为不完整；诊断不增加调用数或token用量。

有效但不完整轨迹退出0并显式标不完整；无法读取/格式不符退出2，输出固定错误码，
不回显路径、原始错误正文、密钥或未知字段。仅提供本地统计，不代表完整平台审计API已完成。

## 供应商核对（2026-10-18）

[官方接口](https://api-docs.deepseek.com/api/create-chat-completion/)支持工具调用及显式关闭思考；
[价格页](https://api-docs.deepseek.com/quick_start/pricing/)当日列出Flash峰时每百万token：
缓存命中输入USD0.006、未命中输入USD0.30、输出USD1.20，非峰时减半。
价格与别名指向的版本会变化，重跑时复核；账单以供应商为准，估算不等于实际扣费。

[官方隐私政策](https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html)覆盖API，
不能据此声称零保留、企业保密认证或自动豁免训练。只允许公开/人工构造资料测试，
不得发送真实企业保密内容；本平台没有自动识别敏感信息的能力。
真实测试必须显式SMARTDOCS_LIVE_DEEPSEEK=1，文件test/e2e/m3_deepseek_test.py
在unittest发现模式下无显式开关时跳过，pytest不收集；最多6次请求，使用人工公开文本，未获成功也不自动重跑。
