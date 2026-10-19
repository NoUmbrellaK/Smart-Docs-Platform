# A22 保留 MCP 超时原因与未知写入语义

2026-10-19，基线 `f817fcfe824b26076c7e23718765e6f06f10f6c3`。对应 T-19 和 T-16 的局部证据，
不代表 M3/P0、真实模型效果或真实网络超时验收。

## 根因与修复

`mcp_read.backend_read` 已将 TimeoutError 规范化为 timeout，CLI 工具轨迹也正确记录；
但 `agent_runner.run_task` 把搜索/读取、报告保存、方案创建的该状态合并为 dependency_failed。
仅改三处映射，复用已有 timeout 错误码，无新依赖、C++ 改动或 schema 迁移。

- 搜索/读取超时：终止为 failed/timeout，停止后续模型和工具调用。
- 报告/方案写入超时：保持 attempted 标记，只查询持久结果；已提交则展示真实回执，
  查不到则 unknown/timeout。不自动重写、不用失败 finish 覆盖可能仍在提交的操作。
- 确认后的归档执行原本已保留 timeout，本轮未修改；权限、确认、幂等和外发检查不变。
- 历史 dependency_failed 不猜测回填；生产未部署。

## 实际测试

[初次 RED](./initial-red.txt)：6项中5项失败。其中4项是错误码丢失，另1项是测试错误地
要求成功报告回执不存在 error_code 字段，而真实契约为 null；修正该断言，不修改产品适配夹具。
[确认 RED](./red.txt)：同6项，4失败、2通过。再修改产品。

[完整 MCP/Agent 回归](./mcp-green.txt)：**94/94**，73.775秒，0失败、0跳过。
新增6项覆盖搜索/读取失败、报告/方案未获回执仍未知，以及两类写入已提交后的查询恢复。
模型和业务权威使用本地接收器；真实 HTTP 请求处理后注入 TimeoutError，经实际 MCP
backend_read、in-process SDK、CLI审计与执行器。每项只派发一次，不泄露异常详情。
这些测试不是 C++/SQL 或 stdio 验证。

[独立 C++/SQL 验证](./sql.txt)：**4/4**，3.486秒，0失败、0跳过。
实际上传和批准人工资料，创建任务，经 in-process SDK、执行器、C++ 和独立临时库。
搜索/读取响应丢失后，任务与 agent_runs.finish_code 实际保存 timeout；
报告已提交则返回唯一报告回执，方案已提交则等待真人确认且无操作ID。每项只派发一次，
模型仅调用一次，方案/报告数量准确、确认记录为0、文件目录不变。
C++已处理的读取轨迹为 processing（夹具未解析），搜索/报告为success，方案为waiting_confirmation；
MCP客户端未收到这些响应，因此其本地轨迹为timeout，两层状态不能混为一谈。

故障均由测试在收到响应头后抛 TimeoutError 注入，并非实际等待15秒、物理断网或真实模型超时。
SQL验证中写入已经提交；未知未提交写入只在6项本地接收器测试覆盖，不扩大证据范围。
无真实 key 读取、供应商请求或模型费用。只读审查核对三处映射、六项测试及四项集成测试，
未发现重要问题；审查未执行测试，不代替运行结果。

## 复现与资源

在既有受限副本 `/tmp/smartdocs-safe.h4SzvX/repo`，使用
`/tmp/smartdocs-mcp.P7grAI/venv/bin/python`，PYTHONPATH包含该副本的 python 和 test/e2e：

```sh
# 首次RED及确认RED均使用此六项选择；修复后包含在完整回归中。
python -B -m unittest discover -s test/mcp -p test_agent_runner.py -k test_mcp_ -v
python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_tool_timeout_test.py
```

以上 python 指所列虚拟环境，不是系统Python。所有命令分别串行通过 systemd-run 运行：
`--slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077
-p NoNewPrivileges=yes -p TimeoutStopSec=5 -p KillMode=control-group`。
RED上限180秒、完整MCP300秒、SQL900秒；SQL设置
`SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python`。
数据库包装器使用新私有socket临时库，32MiB缓冲池，不连接业务库。

共享限额维持CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、Tasks256，
磁盘读写各5MB/s及200IOPS；可用内存低于512MiB停止本轮单元，不提高限额。
[最终状态及哈希](./resources.txt)确认运行源码/测试与工作树一致；未改组件及三个二进制
仍与A20哈希匹配（执行器是本轮唯一产品改动）。所有单元MainPID=0，slice TasksCurrent=0；
结束可用内存1248MiB，high保持80521，max/oom/oom_kill均0，无新增节流/OOM，原MySQL仍active。
不重建C++或重复计算上轮其他套件成果。
接口语义见[说明](../../../api/m3-deepseek.md)。真实供应商、多步效果、固定业务任务/引用评测、
完整审计及 M3/P0 验收仍未完成。
