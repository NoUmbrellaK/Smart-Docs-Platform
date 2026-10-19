# 归档 MCP 增量验证

日期：2026-10-19。基线 `4dc3ce7`，实际源码/二进制指纹见[source-sha256.txt](source-sha256.txt)。
工作区 `codex/m3-mcp-agent`；源码与nobody测试副本逐文件cmp一致。
本轮没有C++/SQL变更，复用既有schema14二进制，没有重新构建或运行全仓C++测试。

## RED 与真实行为

- [red.txt](red.txt)：4项新增MCP用例中3项失败，缺两个工具注册/路由；无测试错误。
  首次运行有一个断言在SDK异步上下文内被包装为ExceptionGroup，移到上下文外后得到本记录。
- [initial-unit.txt](initial-unit.txt)：初版完整MCP/Agent 61/61；随后新增断连/畸形响应测试并强化执行器白名单断言。
- [initial-regression.txt](initial-regression.txt)：最终MCP/Agent 62/62（53.425秒）；
  随后既有归档22项中21项通过、1项旧夹具失败，后续报告/Python因set -e未运行。
  根因是schema12起run/check要求JSON的tool_request_ids，旧夹具仍发无body请求；
  对照路由、CheckRun和来源台账测试后，仅修正两次夹具调用为空回执列表，未放宽生产校验。
- [e2e.txt](e2e.txt)：真实SDK→C++→隔离SQL新增4/4（8.283秒），含真实stdio子进程枚举五工具及执行。
  提案精确重放不移动；未确认及模型自报批准拒绝；真实网页接口确认后仅提交一次；
  只读/其他任务所有者拒绝、旧确认失效、取消拒绝、未列入提案但在任务范围内的版本撤销阻断、
  目标名称批准撤销阻断；提交后丢弃调用方响应，仍可查询最小回执，撤销后完整执行重放拒绝。

真实链路初次systemd启动误用了相对可执行路径，报 `Failed to find executable scripts/test-with-mysql.sh`，
测试未启动；改绝对路径后使用原资源限额运行。没有放宽权限规则或修改业务库。
应用层丢弃已成功响应不是TCP提交时序故障注入；另有loopback断连验证适配层不自动重试。

## 复现

依赖沿用锁定MCP虚拟环境；不读取DeepSeek配置、不使用远程模型或向量模型。
所有命令在同一限额slice内串行、nobody运行，示例中的python替换为对应虚拟环境：

```bash
PYTHONPATH=python mcp-python -B -m unittest discover -s test/mcp -v
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_archive_mcp_test.py
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_archive_execution_test.py
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_report_mcp_test.py
PYTHONPATH=python parser-python -B -m unittest discover -s test/python -v
```

每个HTTP测试文件使用自己的独立临时库，不能共用已有管理员夹具。
MySQL初始化的空密码告警属于仅临时本地socket测试库，不是生产配置；SDK未知工具告警属于拒绝路径测试。

最终[regression.txt](regression.txt)：修正旧夹具后的归档HTTP/SQL回归22/22（19.392秒），
报告SDK/stdio/C++/SQL回归4/4（8.207秒），既有Python106/106（21.339秒），无失败/跳过。
归档覆盖事务中途故障全回滚、并发同操作单次提交、等待状态重启保留、预算不重置及新版本/目录/位置冲突。
连同前述62/62单测与新增4/4真实链路，构成本轮验证范围；不把迁移检查和重复跑次累加作独立场景。

## 资源与未覆盖边界

单机约3.5GiB RAM，无swap；slice CPUQuota=100%、MemoryHigh=1200MiB、MemoryMax=1600MiB、
MemorySwapMax=0、TasksMax=256、磁盘读写各5MB/s/200IOPS，单元最多900秒。
监测MemAvailable低于512MiB即停止本轮测试单元；未运行并行构建、浏览器或模型。
收尾[资源记录](resources.txt)：memory.events的high=80521、max/oom/oom_kill=0均未增加；
测试进程MainPID均为0，业务MySQL仍active，MemAvailable=1362MiB。失败单元状态保留，不代表进程仍在运行。

[只读审查](review.md)无具体问题；本增量按最小实现复用既有传输和C++事务。
归档可信上下文、模型等待/继续、生产HTTP MCP、报告网页、真实模型质量和完整固定任务评测均未覆盖。
五工具协议可调试不代表M3或P0验收完成。
