# A25：本地审计用量汇总

2026-10-19，基线 `30ecdf4ba6fa223785165980bfd5d9c72369a5b6` 加本提交。
新增 [audit_summary.py](../../../../python/smartdocs/audit_summary.py)，读取既有CLI/HTTP
执行器的单份受保护JSONL轨迹，输出已知usage、未知调用及模型/工具分开计时。
复用既有严格JSON解析、usage校验和safe_outcome，不新增依赖或修改执行器/数据库。
用法及字段边界见[接口文档](../../../api/m3-deepseek.md#本地只读用量汇总)。

## 测试证据

- [RED](./red.txt)：9/9失败，均为新增模块尚未存在的明确断言，0.002秒。
- [GREEN](./green.txt)：新增9/9通过，1.683秒；包括真实CLI文件读取、不可变原始文件、
  缺失/非法usage、缓存明细缺失、未结束调用、重复/错配/跨任务事件、截断尾行、
  非法JSON/大小边界、符号链接/FIFO/权限拒绝、无模型归档继续、实际日志writer兼容性。
- [完整MCP回归](./regression.txt)：**103/103**，73.384秒，失败/跳过0。
  其中9项是新增汇总检查；不与原94项或GREEN重复累加。
  SDK对刻意调用未列出工具的旧负例打印提示，结果通过，提示保留。
- 模型writer兼容测试只替换供应商HTTP传输为HTTPX本地MockTransport，实际
  DeepSeekModel/open_audit/summarize均运行。usage为人工20输入+10输出=30的测试值，
  **不是实际供应商用量、模型效果或计费记录**。
- 只读代码审查未发现重要问题。文档注明结构完整不提供防篡改证明，不能发现整段被删改记录。

三个单元在既有 `smartdocsvectorcheck.slice` 下串行运行，uid=nobody/gid=nogroup，
Nice=10、UMask=0077、NoNewPrivileges=yes；新单测RuntimeMaxSec180，回归600。
新单测 `IPAddressDeny=any`；完整回归额外 `IPAddressAllow=localhost`，仅供本地测试接收器。
共享限制仍为单核、MemoryHigh1200MiB/Max1600MiB、SwapMax0、磁盘各5MB/s和200IOPS。
命令核心分别为：

```sh
# 在 /tmp/smartdocs-safe.h4SzvX/repo，由上述systemd受限单元执行
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p test_audit_summary.py -v
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
```

实际单元使用PYTHONPATH绝对路径；RED先只复制测试，GREEN再复制实现。
[环境与哈希](./environment.txt)：Python3.10.12/MCP1.30.0/httpx0.28.1，两个新文件的
工作树/受限副本哈希一致，四个共享依赖cmp相同。`git diff --check`通过。
测试全部退出MainPID=0，slice TasksCurrent=0；内存high事件保持80521，max/oom/oom_kill=0，
无新增内存high事件/OOM，原mysql仍active。临时人工轨迹由TemporaryDirectory测试清理，
未删除真实审计或业务数据；未建测试库、重编译、读取key、发付费请求或部署生产服务。

## 未覆盖/不宣称

这是本机只读运维统计，不是平台完整模型/MCP审计API或网页用量页。
仅汇总单次运行文件，不跨运行去重；没有自动保留/轮转或供应商账单核对。
缺失数据不计免费，cost_estimate保持null；未核实当期价格所以没有费用估计。
退出0只说明可读/结构有效，不表示trace_complete或业务通过；正式使用需先确认写入者已退出。
没有真实模型调用、30项语义任务评测或50条引用评测，M3/P0仍未验收。
