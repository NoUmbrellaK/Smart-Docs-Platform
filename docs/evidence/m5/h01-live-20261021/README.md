# M5：H01 单次真实交接核查失败记录

2026-10-21，代码基线 `7bc4d5196759eae2f35212be0e732e9793dc3744`。
用户单独批准仅运行一次 H01，最多6次请求、每次最多8192输出token；
只允许向固定 `https://api.deepseek.com/chat/completions` 发送该案例六份人工获批资料、
问题、清单、系统提示和工具定义/结果，不发送业务资料或真实用户会话，不确认或执行归档。
本次授权已使用，**失败未重跑，未使用的请求额度不构成下一次运行授权**。

## 结果与原因

**单案例执行失败（0/1 completed），`failed/model_invalid`，采集完整。**
见[机器摘要](result.json)、[逐事件审计](trace.jsonl)与[完整日志/终态](journal-and-status.txt)。
这不是人工业务评分；业务判定仍为空，不将0/1执行完成率冒充固定30项业务完成率。

1. H01创建返回201，六份冻结资料已在隔离环境装载并解析。
2. 首次供应商响应成功返回，`finish_reason=tool_calls`。
3. 执行器在 `tool_batch` 阶段拒绝，**未派发任何模型提出的工具**。
   [执行器](../../../../python/smartdocs/agent_runner.py)要求每批1至4个工具调用；
   审计的 `tool_call_count=5` 使用 `min(len(calls),5)` 饱和计数，证明实际至少5个，
   不能宣称恰好5个，也无法从未留存的原始响应确定工具名称或参数。
4. 后端持久结果是failed，model_calls=1，result=null；工具轨迹为空，
   无报告、归档方案/执行回执或实际引用。采集器7次GET均200且一致。

失败已定位为模型工具批量大小超过本地上限，不是认证、余额、供应商传输失败或内存耗尽。
当前系统提示没有明确告知每批最多4个调用，但仅凭本次不能证明加一句提示必然解决；
后续可先离线验证这一最小提示约束，保留4个上限和写工具必须独占一批的安全规则。
本轮不修改产品、提示、冻结输入、超时或预算，不发起第二次验证。
没有工具结果送回模型，因此本次实际没有向模型发送文件正文；装载器的本地读取不算模型工具调用。

## 实际用量

| 请求 | 输入token | 输出token | 合计 | 缓存命中输入 | 未命中输入 | max_tokens | 供应商耗时ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 2008 | 426 | 2434 | 0 | 2008 | 2560 | 2103 |

模型标识为实际配置的 `deepseek-flash`。报告模式按六项清单计算上限
`min(8192,1024+256×6)=2560`，并非实际输出8192。
用量来自供应商响应usage；未查询当期价格、余额或账单，费用金额不可得，不套用历史单价。

## 运行与安全边界

原ECS（2 vCPU、3495MiB、Linux5.15），Python3.10.12、MCP1.30.0、HTTPX0.28.1；
解析环境单独为pypdf6.21.0。临时MySQL8.0.46使用Unix socket、32MiB buffer、schema16；
仅TXT/Markdown解析与关键词索引，未启用向量编码器。没有浏览器或生产部署。

复用[单案例CLI](../case-cli/README.md)和既有安全副本、server/admin二进制，未新增执行器。
[运行前核对](preflight.txt)中28个既有源/测试/夹具文件与副本逐字节一致，并记录当前哈希；
另核对迁移包装器。历史case-cli清单中的m3_agent_http_test.py已被后续提交更新，
当前工作树与副本一致，不复用该文件的旧哈希。预检曾错误地在MCP环境查询pypdf版本，
返回PackageNotFoundError；随后在独立解析环境确认6.21.0，未安装依赖或因此重跑业务任务。

仅启动 `smartdocs-m5-h01-live-20261021.service` 一次：14:17:15至14:19:05（Asia/Shanghai），
包含限速建库、迁移和清理；不是模型耗时。十条迁移PASS独立于业务分母。
[启动属性](running.txt)：nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、
RuntimeMaxSec600、TimeoutStopSec5、KillMode=control-group；共享slice保持CPU100%、
MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写各5MB/s及200IOPS。
允许已批准API外发；应用固定目的地、禁代理和重定向，不声称网络层域名白名单。

密钥由systemd `LoadCredential=deepseek.env:/root/.config/smart-docs/deepseek.env`
提供。临时库初始化之后，小型启动命令用标准库shlex读取唯一DEEPSEEK_API_KEY赋值，
不执行配置文件，再交给CLI；CLI在启动业务/MCP子进程前移除环境中的key。
密钥没有进入命令行、日志或仓库，原文件权限600 root:root不变。

实际业务命令（仅记录，**不是重跑授权**）：

```sh
# 在上述受限单元/安全副本中，凭据由受保护启动命令提供。
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B \
  test/e2e/benchmark_cli.py --case H01 --live \
  --output /tmp/smartdocs-m5-h01.Mtn9xS/H01 \
  --code-revision 7bc4d5196759eae2f35212be0e732e9793dc3744 \
  --environment '上述实际ECS/依赖/关键词索引/隔离库配置' \
  --max-calls 6 --hold-seconds 0
```

终态MainPID=0、slice TasksCurrent=0，原mysql仍active；单元failed是CLI失败退出码1，
不是服务器崩溃。memory.high前后82471，max/oom/oom_kill均0；离散内存观测不代表峰值。
包装器已删除本轮临时库/上传原件，不能直接恢复；未删除业务数据或密钥。
私有快照、审计和空白评审表保留在 `/tmp/smartdocs-m5-h01.Mtn9xS/H01`（目录0700/文件0600），
哈希见机器摘要；会话context和审核登录文件不残留。原始快照不提交仓库。
离线review工具的snapshot_sha256是规范化JSON摘要，与文件字节SHA不同，不能混用。

## 固定分母与未完成项

[台账](../fixed-task-results.json)仍为30项：3项准入拒绝观测、1项真实模型失败、
26项模型任务未运行。人工业务判定0、实际引用0，业务完成率和引用准确率均不计算。
冻结tasks.json原有not_run是冻结时历史状态，不回写；两份夹具SHA保持不变。
本轮仅新增真实失败证据和离线诊断，M5/P0仍未完成。

[离线一致性检查](verification.txt)核对单次请求配对、2560输出上限、2434实际token、
失败码、30项分母、冻结夹具哈希及276个本地链接目标；凭据模式检查和git diff --check通过。
只读审查发现README遗留的“无付费调用”表述与本轮不符，已修正。
这些检查不是第二次真实运行或业务验收通过，不为失败记录补造报告或引用。
