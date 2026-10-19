# 可信归档上下文与提案 Agent 验证

日期：2026-10-19，基线 `0eb7d8d`，分支 `codex/m3-mcp-agent`。
采用最小实现：复用任务表快照、既有归档策略/事务、MCP和本机执行入口，无新增依赖或服务。
实际源码和被测二进制指纹见[source-sha256.txt](source-sha256.txt)，源码与nobody副本逐文件cmp一致。

## 失败先行与修复

- [初始真实链路RED](red-e2e.log)：6/6失败，旧任务接口拒绝新绑定字段。
- [初始执行器RED](red-unit.log)：6项中4项失败、2项通过，归档上下文/提案流程尚未实现。
- [大预览RED](red-boundary.log)：7项中1项失败。有效100项短理由提案补入文件/目标名称后超过64KiB，
  提案已提交但协调错误返回unknown。只将归档预览读取/工具响应限制调整为256KiB，
  不扩大模型输入预算、不把完整预览发送给模型。见[只读审查与复查](review.md)。
- [审计状态RED](red-audit-status.log)：真实链路7项中1项失败，等待确认被CLI审计白名单误记为依赖失败。
  增加waiting_confirmation后复验，不修改C++授权或未知状态处理。

## 验证范围与复现

[构建日志](build.txt)：`make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin` 成功。
之后修正schema测试的版本期望并增量编译，记录于[单测日志](unit-green.txt)。
MCP/执行器 **69/69**（57.083秒）、既有Python **106/106**（21.396秒），无失败/跳过。
覆盖输出预算经真实供应商适配器进入本地HTTP模拟接收端、工具范围隔离、
混批拒绝、错误修订不发送、未提交断连仅返回未知、100项大预览协调及无失败finish。

本机依赖沿用锁定MCP和parser虚拟环境，不读取DeepSeek配置、不调用付费模型或远程向量服务。
以下命令必须在受限slice中串行、非root运行；mcp-python/parser-python替换为对应虚拟环境解释器：

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
PYTHONPATH=python mcp-python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python parser-python -B -m unittest discover -s test/python -v
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_archive_agent_test.py
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_archive_execution_test.py
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_handover_context_test.py
scripts/test-with-mysql.sh -- mcp-python -B test/e2e/m3_report_mcp_test.py
scripts/test-with-mysql.sh -- /bin/bash -c '
  set -e
  stdbuf -oL bin/smartdocs_tests --filter=mysql_schema
  stdbuf -oL bin/smartdocs_tests --filter=auth_project
  stdbuf -oL bin/smartdocs_tests --filter=m3_
  stdbuf -oL bin/smartdocs_tests --filter=file_mutation
  stdbuf -oL bin/smartdocs_tests --filter=authorization
'
```

HTTP测试每个文件用独立临时MySQL/socket，不复用管理员夹具。初始化空密码告警仅属临时socket库；
SDK未知工具告警是拒绝路径测试。wrapper包含旧库升级schema15、重放、半套DDL拒绝和103文件保留检查。
启动运行需传 `PYTHONPATH=python`，真实链路另传 `SMARTDOCS_TEST_PARSER_PYTHON=parser-python绝对路径`。

## 最终真实链路与C++回归

[真实链路日志](http-green.txt)：归档Agent **8/8**（7.287秒）、既有归档 **22/22**（20.804秒）、
交接上下文 **8/8**（7.578秒）、报告SDK/C++/SQL **4/4**（6.810秒），均无失败/跳过。
新链路覆盖显式绑定/修订确认、未批准和读者拒绝、目标重命名/撤销阻断、
动态搜索→读取→提案后无第四次模型请求、真实确认后独立执行、提交后丢弃回包、取消以及快照重启保留。
保存方案不会移动文件；只有测试显式经真实确认接口后另行执行才产生移动。
既有回归继续覆盖整批故障回滚、并发单次提交、旧确认失效、来源撤销和个人报告幂等保存。

[C++分组日志](cpp-green.txt)：schema **6/6**、auth_project **3/3**、m3_ **17/17**、
file_mutation **3/3**、authorization **2/2**，合计 **31/31**，无失败/跳过。
这是本轮相关分组，不是全仓C++全量复验；不把重复迁移检查累加为新的独立场景。

## 资源与限制

当前ECS约3.5GiB内存、无swap；CPUQuota=100%、MemoryHigh=1200MiB、MemoryMax=1600MiB、
MemorySwapMax=0、TasksMax=256，磁盘读写各5MB/s/200IOPS，单元RuntimeMaxSec=900。
监测MemAvailable低于512MiB即停本轮测试；没有并行编译、浏览器或模型负载，也没有放宽限额。
[收尾资源](resources.txt)：所有本轮单元MainPID=0；失败单元仅保留RED状态。
memory.events的high=80521、max/oom/oom_kill=0与运行前一致，没有新增内存节流/OOM事件。
业务MySQL仍active。测试临时库由既有wrapper清理，不删除用户资料；可通过测试命令重建。

schema15仅在隔离测试库升级，未迁移或部署原业务服务。脚本模型验证真实工具/SQL编排，
不代表DeepSeek分类质量、完整归档准确率或T-01至T-20验收。
应用层丢弃成功回包不是所有TCP提交时序的故障注入；另有本机断连单测覆盖未知结果不抢写失败。
确认后继续编排、网页归档任务选择、个人报告网页、生产HTTP MCP和完整固定任务集仍待完成。
本增量不宣称M3或整个P0完成。
