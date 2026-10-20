# M5：同一次任务的只读结果留存

2026-10-20，代码基线 `44f9ac1` 加本提交。新增
[benchmark_capture.py](../../../../test/e2e/benchmark_capture.py)，连接已有固定任务装载器与
[离线引用评审](../../m3/benchmark-review/README.md)。不改生产接口或数据库，
不新增模型执行器、依赖、付费入口或自动归档确认。

后续已增加[单任务执行连接层](../fixed-execution/README.md)，复用既有Agent后再调用本采集器。
它不是批量付费评测或长期人工确认会话管理器；本文下述结果仍为采集器自身的历史证据。

## 使用边界

在 `FixedInputsE2E.prepare_case(case)` 所创建的隔离库/任务仍存活时，由同一次运行的调用方使用：

```python
from benchmark_capture import capture, write_snapshot

# prepared 是本次 prepare_case 的返回值；含私有 owner client，不能打印/持久化整个对象。
# 调用方先通过既有运行入口执行任务。采集器本身不会启动、重试或完成任务。
snapshot = capture(case["id"], prepared, {
    "code_revision": actual_commit,
    "recorded_at": actual_timestamp,
    "environment": actual_environment,
    "model": actual_model_or_no_model,
    "evidence_path": private_run_evidence,
}, run_kind="transport_fixture")  # 协议夹具必须使用此值，不能声明为真实供应商任务。
write_snapshot(new_private_path, snapshot)
```

真实供应商执行须另获外发/费用授权，才可使用 `live_provider` 声明。准入拒绝用
`admission_rejection`，保存真实准入状态，不制造运行记录。声明和元数据不是签名或执行证明。
此函数不是一个会自动执行30项的命令；仍须由受控执行流程调用，且在夹具清理前保存结果。
真实确认也必须在正确方案修订上单独进行，采集器不会代替用户确认。

采集内容：

- 后端任务状态、完整运行结果、最多64条服务端工具请求轨迹、固定夹具ID/版本/哈希映射。
- 报告正文及与运行结果匹配的回执；归档计划（含已有决定）、已执行归档的实际回执。
- 每条引用指向的固定版本原文片段、位置、原文哈希；相同出处只读取一次，
  但评审中的引用出现次数不去重。先重新检查整份运行结果的依赖，再读取片段。
- 所用冻结夹具文件哈希与各次GET的HTTP状态。只使用构造出的本机路径，不跟随模型/报告提供的URL。

采集器只发GET，不发模型请求、不确认或执行方案、不直接访问数据库。
“只读”指采集器不调用写入口，不保证后端GET没有既有状态维护副作用：
例如普通已完成问答的结果GET发现范围被撤销时，现有C++会将任务置为failed并清除旧结果；
采集器不绕过这个检查或将缓存结果继续标为有效。
获取期间遇到非200、无效结构、身份错配、连接失败或前后读取不一致，
标记 `capture.complete=false`；离线评审拒绝此类记录，不把错误响应或异常文本存入快照。
已成功读取的部分内容保留作私有诊断材料，不因后续拒绝而当作可发布结果。

`capture.complete` 仅表示这次采集检查完成，不表示任务完成或业务正确。
顺序GET不是数据库原子快照，复读只能发现可观察到的变化，不能证明期间没有短暂变更，
也不能保证采集后的权限仍有效。轨迹达到64条会额外标记，不能据此声称历史完整或模型费用准确。
尚未创建运行记录的pending任务、没有可读结果的中断等可以留下不完整记录，不能伪装成成功。

输出独占创建为0600，最大2MiB，不覆盖文件或符号链接。可能含原文和模型输出，
**不得提交仓库、公开上传，或执行其中的文字/链接/指令**。只持久化采集函数输出，
不要把client、Cookie、key、Authorization或账号密码放进metadata/映射。
中断写入可留下不完整文件；保留排查，重新采集用新路径，不自动重跑模型。

## 验证范围

新增 [单元边界测试](../../../../test/python/test_benchmark_capture.py) 与
[真实HTTP/隔离SQL测试](../../../../test/e2e/m5_benchmark_capture_test.py)。
真实后端测试仍是人工写入的协议结果，不是供应商回答或固定业务评测：
覆盖Q01重复引用被拒绝后保存单一引用、H01多个报告项共用出处仍逐次计数及撤销访问、
A06待确认方案、A01已执行归档回执、Q08准入拒绝、Q02失败结果。
A01的确认/执行仅是隔离测试的显式准备动作，不是采集器自动确认或真实用户业务验收。

运行时沿用 `smartdocsvectorcheck.slice`：CPU 100%、MemoryHigh 1200MiB、
MemoryMax 1600MiB、Swap 0、磁盘读写各5MB/s及200IOPS，nobody、Nice 10、
NoNewPrivileges，禁止外网、仅HTTP测试允许localhost。隔离MySQL串行初始化；
测试不读取真实key，不改生产库。

实际在预先准备的 `/tmp/smartdocs-safe.h4SzvX/repo` 受限副本执行；新增/修改的四个Python
文件与工作树逐字节一致。沿用此前受限构建的server/admin二进制，没有重新编译或使用
工作树中较旧的bin；本增量没有改动C++。启动命令如下（路径是本机既有环境，不是通用安装步骤）：

```sh
systemd-run --unit=smartdocs-m5-capture-verified --slice=smartdocsvectorcheck.slice \
  --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=900 -p TimeoutStopSec=5 -p KillMode=control-group \
  -p IPAddressDeny=any -p IPAddressAllow=localhost \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e \
  --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  /bin/sh -c 'set -e; /usr/bin/python3 -B -m unittest discover -s test/python -p "test_benchmark_*.py" -v; scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m5_benchmark_capture_test.py'
```

过程记录（所有失败保留，不以最终通过改写）：

- [基线](./baseline.txt)：原离线评审8/8，0.251秒。
- [首次RED](./initial-red.txt)：新增5个方法均因采集模块缺失失败。
- [首次HTTP](./http-fixture-failure.txt)：5个方法中4过1失败；Q01测试向问答保存接口提交
  重复引用，已有后端按契约返回400。修正夹具为先验证400再保存单引用，不修改产品门禁。
- [传输边界RED](./transport-red.txt)：截断响应的 `HTTPException` 未被捕获，
  5个方法中1个错误。增加标准库异常处理，返回脱敏不完整记录；不重试请求。
- [回执夹具失败](./receipt-fixture-failure.txt)：14个单测通过，6个HTTP方法中5过1失败；
  新增A01测试错误地将含items的执行响应与六字段查询回执比较。根据后端契约修正预期字段；
  采集器仍保留真实GET结果，没有删除或伪造响应字段。
- [最终复验](./verified.txt)：单测 **14/14，0.282秒**；真实HTTP/隔离SQL **6/6，11.167秒**。
  22:33:20至22:35:18 CST包含受限MySQL初始化、迁移检查及清理，不能将11.167秒作为总运行时间。
  没有实际模型请求；后端model_calls是协议准入计数，不能作为供应商调用次数。
- [运行中限额](./running-limits.txt)记录前一轮测试实际属性；最终轮沿用相同启动参数。
  [结束状态](./status.txt)及最终日志显示测试退出、MainPID=0、原mysql仍active。
  transient unit卸载后部分属性回到systemctl默认值，不能将结束查询中的默认值当成运行配置。
  memory.events 的high始终80521，max/oom/oom_kill均0，没有新增内存high或OOM事件。
- 静态核对保持冻结任务30项、not_run及tasks.json哈希不变；
  四个新增/修改Python文件与受限副本一致。无生产部署或全仓回归。

本增量不改变冻结30项的 `not_run` 状态，不产生业务完成率或实际引用语义准确率。
M5仍需受控真实任务执行、全部引用人工评审、T-01至T-20整合、匹配版本部署验证及完整演示；
M1至M4功能阶段结论不变，整个P0尚未完成。
