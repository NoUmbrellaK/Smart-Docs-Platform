# A31：一次限定 DeepSeek 公开资料测试

2026-10-20，代码基线 `ac29088`；本提交只更新证据及文档，没有修改生产代码、提示词或测试断言。

## 结果

**真实测试 0/1，8.107 秒，任务终态 failed/model_invalid。** 不能宣称回答已保存、
引用正确或 M3 已验收。完整[脱敏日志与终态](./journal-and-status.txt)及独立
[事件轨迹](./trace.jsonl)保留失败，不重跑、不用替身成功覆盖真实失败。

| 实际顺序 | 观测 |
| --- | --- |
| 模型请求1 → 搜索1、2 | 两次搜索均 success，命中0 |
| 模型请求2 → 搜索3、4 | 相对前次 query_changed=true；依次命中0、1 |
| 模型请求3 → 读取5 | read_document_version success，返回1个片段 |
| 模型请求4 → 执行器收敛 | 供应商传输 success；任务 failed/model_invalid，completed状态断言失败 |

轨迹证明本例真实模型在收到空结果后调整了查询并读取候选正文，补充了 T-10 的动态决策证据。
测试在completed状态断言处退出，后续回答保存及引用检查未执行；
不能用这个局部结果代替最终回答/引用验收、完整业务任务、真实浏览器演示或 M3 阶段关闭。
工具参数和回答未进入日志，不能从 query_changed 推断具体改查词或来源支持关系。

## 授权与费用

用户已明确批准一次限定测试，并于2026-10-20恢复工作。只允许人工公开 ORBIT-27 部署说明、
测试问题、系统提示和只读工具定义/结果，固定目的地
`https://api.deepseek.com/chat/completions`；不发送真实业务资料或用户会话。
已有 key 通过 root:root 0600 环境文件注入，未展示或保存到仓库。
原批准最多6次请求、每次最多512输出token；本次实际4次，各请求的 max_tokens 均512。
这一次测试已消耗该次运行授权；**不因尚余2次额度而自动重启测试**，也不扩大到30项评测。

| 请求 | 输入 | 输出 | 合计 | 缓存命中输入 | 缓存未命中输入 | 模型耗时ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1059 | 114 | 1173 | 0 | 1059 | 1237 |
| 2 | 1539 | 117 | 1656 | 1152 | 387 | 1062 |
| 3 | 2361 | 126 | 2487 | 1536 | 825 | 1239 |
| 4 | 3037 | 282 | 3319 | 2432 | 605 | 1767 |
| 合计 | 7996 | 639 | 8635 | 5120 | 2876 | 5305 |

以上为响应 usage，不是供应商账单。按2026-10-20核对的
[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)，deepseek-flash
非高峰每百万token缓存命中输入$0.003、未命中输入$0.15、输出$0.60；
调用时间12:51 UTC落在非高峰。估算
`(5120×0.003 + 2876×0.15 + 639×0.60)/1000000 = $0.00083016`，
实际扣款以账户账单为准，不声称已查余额或账单。

## 离线诊断及证据边界

4条 model_result 均 reason=success，说明固定HTTPS传输、供应商响应封套及usage解析成功；
不是本次账号认证失败、余额不足、429或网络超时。
第4次响应后没有新 tool_attempt，直接进入 task_outcome/model_invalid。
代码中普通回答的最终JSON解析、字段校验、finish_reason及工具批次/调用ID校验，
以及其他未分类异常，都可能归并到该错误。

因此只定位到**供应商返回后的执行器处理阶段**，不能精确归因到Markdown围栏、
JSON字段错误、finish_reason或某个具体引用。原始回答没有落盘，临时文件已清理；
现有脱敏轨迹缺少响应形状和拒绝分支信息，无法离线重放该实际响应。
不凭猜测放宽JSON/引用校验，也不把所有 model_invalid 宣称为同一个已知缺陷。
后续可先以本地测试补充固定枚举的拒绝原因、finish_reason及形状计数，
继续禁止正文/凭据日志；再根据证据决定最小修复。再次真实调用须另获明确限定批准。

## 环境、执行与清理

仍在当前ECS，用已准备的安全副本和二进制串行执行：

```sh
systemd-run --unit=smartdocs-m3-provider-live-20261020 \
  --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup \
  -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group \
  -p EnvironmentFile=/root/.config/smart-docs/deepseek.env \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  --setenv=SMARTDOCS_LIVE_DEEPSEEK=1 \
  --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e \
  --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_deepseek_test.py
```

此命令仅记录本次执行，不表示授权复制重跑。临时路径不保证在新环境存在。
与离线单元不同，本单元未设置仅回环IP限制，以便连接已批准API；
应用固定HTTPS目的地，禁代理、重定向、重试；并非网络层仅允许该域名。
CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、SwapMax0、
磁盘读写各5MB/s和200IOPS保持原限额。隔离MySQL只用Unix socket、
32MiB buffer pool、关闭performance_schema；schema16迁移及回放检查完成。
日志中空密码警告来自隔离库初始化，不是修改原业务MySQL密码。

运行时间20:49:43至20:51:36 CST，含数据库准备和清理；8.107秒仅为测试计时。
[运行中观测摘录](./resource-observations.txt)的MemAvailable最低2119096KiB
（约2069MiB，不是连续最低值），结束2314604KiB；
memory.events保存终态high80521，与运行前会话观测一致，max/oom/oom_kill均0，
本次观测无新增内存节流或OOM。
最终MainPID=0、slice TasksCurrent=0，原mysql active；未部署或迁移业务库、未重新编译。
wrapper及测试TemporaryDirectory已清理本轮人工测试库、文件和私有上下文，
不可直接恢复；保留源码和脱敏日志，不包含真实业务资料。

执行副本与工作树以下5个文件逐字节一致，二进制复用此前构建：

```text
ce7048199cb9aebd1c4098a0fe3502650898929c4c65cf8b495e011922dd4471  test/e2e/m3_deepseek_test.py
1c4aa360417be884bca83000e9de0e194dee731a875534faec682da25498e6bb  python/smartdocs/deepseek.py
a6b1da54ea9334cf81726256d0e38106067186b7aecbc26f3774359bddfee784  python/smartdocs/agent_cli.py
1534fc7c4ca1952187677b8e9f89aa7e3b84ab17f0a52dd8c167fc447d647d88  python/smartdocs/agent_runner.py
ae010be5e53010885ed7e3d42ced2729169e5427099e9b0c3d74a669d181aaaf  python/smartdocs/mcp_read.py
024a2e8fa4c1712049fd159dc781e4fe0e2de5869646cf5e04e58fd5b93db97d  bin/server
```

## 文档一致性检查

标准库离线检查通过：JSONL逐事件等于原日志中的轨迹，4次attempt/result匹配、
每次512输出上限、usage及费用算术一致、最终失败状态一致；四份本轮涉及的Markdown
共200个本地链接目标存在。5个源码SHA重新计算匹配上表，`git diff --check`通过。
这些检查不调用API、不运行产品回归，也不改变真实测试0/1的结论。
