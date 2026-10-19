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

401/402/429/5xx等返回通用dependency_failed，受控轨迹分别记录provider_auth、
provider_balance、provider_rate_limit、provider_unavailable；不记录供应商错误正文。
超时返回timeout，超次数/请求大小返回budget_exhausted，非法模型结果终止。
隐藏推理与未知响应字段不返回给核心；无第三方追踪。

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

这是受控本地运行证据，尚不是数据库可查询、可分页的完整模型/MCP任务审计API；
C++现提供[后端请求轨迹](m3-tool-audit.md)，不能等同本地模型usage或MCP耗时。
平台完整调用摘要、生产内部HTTP认证和审计保留策略仍待后续接入。

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
