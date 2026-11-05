# 工具派发前拒绝诊断

2026-11-05，基线 `c6efd5a968163fc953d9928af64f4d8042bc0527` 加本提交。
针对[H01第二轮](../h01-batch-retest-20261105/README.md)暴露的观测缺口，
补充未来运行的私有诊断；不是重放、恢复旧响应或证明H01已修复。

## 改动与边界

复用现有审计增加非终止性tool_rejection，区分非法JSON、未知工具、schema错误、
报告操作ID不匹配、归档修订不匹配。记录模型序号、含拒绝的工具序号、固定工具名及
首个错误的静态schema路径；未知名称统一unknown，不记录实例路径、参数值、ID或错误正文。
汇总器检验事件配对、递增序号、白名单和schema路径，将拒绝与实际工具派发分开计数。
旧日志未记录时为空，不据此推断没有拒绝。字段见[接口说明](../../../api/m3-deepseek.md#工具派发前拒绝诊断)。

校验、预算、模型收到的invalid_request、重复错误终止及不确定写入处理保持原规则；
审计失败收敛为dependency_failed并停止派发。无新依赖、业务schema、提示词、
C++或前端改动；未读取真实key、调用API、部署服务、确认归档或重编译。

## 验证

- [初始RED](initial-red.txt)：4项、8 failure/1 error，含测试重复调用ID导致提前拒绝的问题。
  修正测试ID及预期失败表达后，[有效RED](red.txt)仍4项未通过、9个子用例失败，1.734秒：
  缺少tool_rejection、审计错误未触发停止、汇总器不识别新事件。不是业务任务失败率。
- [GREEN](green.txt)：4/4，2.277秒。
- [完整MCP/Agent回归](regression.txt)：113/113，78.724秒，0失败/跳过。
  SDK未列出工具警告来自既有负向用例，原始日志保留，不声称无警告。
- 独立只读审查无阻断问题；补充expected_revision=1和报告嵌套required路径两个子用例后，
  [最终定向及全部汇总器检查](review-check.txt)14/14，4.229秒，0失败/跳过。
  产品源码自完整回归起未再修改；14项包含已跑过用例，不与113项相加。

用例执行真实Agent、MCP SDK、适配器审计和汇总器；收费提供商使用HTTPX本地替代，
业务权威服务是回环协议接收器，不冒充真实模型或C++/SQL链路。
验证未知名称/私密标记不进审计、非法调用不派发、同批合法读取仍可执行、后续可完成、
绑定失败和重复错误不产生写操作、日志写失败中止、畸形/重复/孤立事件拒绝及旧日志兼容。
缺失notes示例是人工离线夹具，不是H01真实错误的还原。

## 环境、复现与剩余工作

当前ECS原安全副本 `/tmp/smartdocs-safe.h4SzvX/repo`，Python3.10.12/MCP1.30.0/HTTPX0.28.1。
全部单元nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax300s、
TimeoutStop5s、KillMode=control-group、IPAddressDeny=any且仅允许localhost。
沿用smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
Tasks256、磁盘读写各5MB/s及200IOPS；无并发重负载。

完整回归命令（放入上述受限单元）：

```sh
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
```

各定向命令和单元时间见对应日志；时间为Asia/Shanghai。
结束MainPID=0、slice TasksCurrent=0、原mysql active；memory.high前后82471，
max/oom/oom_kill均0，无新增内存节流/OOM。MemAvailable采样2157/2087/2141MiB，非连续峰值。
只清理测试自身TemporaryDirectory中的合成审计，无业务数据删除。

最终四个源码文件与安全副本cmp一致，SHA256如下：

```text
b3ff0b883ecd83e68e534658a8dded91293a17cae4347832229d9b99b9e4712f  python/smartdocs/agent_runner.py
279d07ef0369eb4009756e3820e619f245eda8ed6be5eb68ff4f5323d2292cd2  python/smartdocs/audit_summary.py
6bb4a3e3d471208c57b6f428f57da12d120c306fef592344b158e6d419bfff47  test/mcp/test_agent_runner.py
9c8439cbdf48ff70931eb5aa0ca60189a63e15dc193f8eecd9d7d0dd93eec9bb  test/mcp/test_audit_summary.py
```

H01仍是两轮未完成、累计7次外发/51349 token；本增量0次外发、业务台账不变。
后续可按用户2026-11-05持续同范围授权运行有诊断的受限测试，无需逐次批准；
仍须控制用量、不发送业务资料、不代替真实归档确认。
M5/P0仍未完成；该增量不证明模型任务完成率、引用语义准确率或已完成生产部署。
