# M5：单案例评测命令与限时人工审核现场

2026-10-21，基线`9630a81`。新增
[benchmark_cli.py](../../../../test/e2e/benchmark_cli.py)，复用固定夹具、
[单任务执行/采集](../fixed-execution/README.md)与已有网页。没有新模型循环、依赖或生产变更。
一次命令只处理一个冻结案例，不自动批量调用或重跑失败案例。

## 入口与产物

必须指定case、全新输出目录、代码版本、实际环境描述、模型调用上限1–8、
人工审核保留时间0–600秒。模式必须显式二选一：

- `--admission-only`：只接受Q08/Q09/A09；不读取key、不启动Agent。
  若环境中已有key，仅删除该环境项，避免fixture子进程继承。
- `--live`：其他案例才可使用；另外取得数据外发/费用授权后才允许运行。
  从环境取出key并移除，再启动子进程。不读取配置文件，不自行申请授权。
  只读输出最多512 token；报告/归档结构化输出可更大，见执行连接层说明。

**本轮没有运行任何真实付费案例；之前三次烟测授权已经消费，不能复用。**
`--live`只是操作门禁，不等于用户已授权。下面仅示意不调用模型的命令，
仍应放在既有资源限制的systemd单元内，并使用已配置依赖的Python：

```sh
PYTHONPATH=python:test/e2e bash scripts/test-with-mysql.sh -- \
  /path/to/mcp-venv/bin/python -B test/e2e/benchmark_cli.py \
  --case Q08 --admission-only --output /path/to/private-parent/Q08 \
  --code-revision ACTUAL_COMMIT --environment '实际硬件、依赖和索引配置' \
  --max-calls 1 --hold-seconds 0
```

必须由既有`test-with-mysql.sh`创建独立临时库。CLI不是生产数据操作入口，
不负责对现有业务库迁移、停止服务或部署。输出父目录必须可信且私有，
输出目录本身排他创建为0700；所有JSON和审计文件0600。

产物：

- `initial/snapshot.json`及普通Agent任务的`initial/audit.jsonl`：执行一次后的原始采集。
- `after-review.json`：仅实际进入审核等待流程时保存，独立保留初始方案与最终状态。
- `summary.json`：状态、采集是否完整、等待结果；business_verdict永远不自动填写。
- `failure.json`：普通fixture/执行异常时的无敏感详情诊断；已产生的审计和快照保留。
- `review-access.json`：只在审核期间存在的私有测试账号/本地URL/任务定位说明；
  原子发布，正常退出、普通异常和Ctrl-C时删除。不得提交、复制到公开日志或发送给模型。

退出码：0=已采集completed或规定准入拒绝，3=待确认/待执行，1=失败或采集不完整，
2=配置/门禁拒绝。**0不等于业务评审通过**；模型语义、来源支持关系仍需独立评审。
硬杀、系统崩溃或写盘失败不能保证完整产物；缺失结果不是重跑授权。
公开证据只保留脱敏摘要，完整快照含资料/来源，不提交仓库。

## 人工审核

已生成waiting_confirmation/waiting_execution时，`--hold-seconds`非0会维持当前隔离HTTP服务。
操作人私下读取`review-access.json`，在服务器本机访问其`/app.html`；
远程操作须使用受控SSH端口转发并保持同源，不能把测试服务开放公网。
登录文件中的临时账号，按项目ID和任务ID找到方案：

1. 人工查看每个文件、版本、原/目标目录及理由。
2. 在已有网页勾选并确认**具体修订**，也可以拒绝或修改。
3. 需要移动时，另点“Execute confirmed revision”；确认本身不移动文件。
4. CLI只GET任务状态，不提交确认/执行，不调用自动resume；观察到终态后采集实际回执。

等待截止时仍未完成，就保存待确认/待执行状态并返回3，随后fixture清理取消未完成任务，
数据库/存储由测试包装器清理。**截止后该网页现场不能继续使用**。
不能依赖默认900秒运行上限无限等待人；保留时间还应给初始化和模型执行留余量。
这不是长期任务托管。A07/A08等案例特定的交错修改仍须按冻结输入安排，
本入口不把“运行了模型”替代这些步骤，也不自动把模拟确认记为人工验收。

## 本轮验证范围

[测试](../../../../test/e2e/m5_benchmark_cli_test.py)使用真实Agent/stdio MCP/C++/隔离SQL，
仅模型网络使用MockTransport。独立测试线程读取私有登录说明、登录后确认并执行A01，
证明CLI在等待期间没有关闭现场，最终采集到同一任务的执行回执。
**这个线程是明确的synthetic reviewer，不是真实用户，不计入30项业务完成率。**

其余检查：帮助不依赖key/数据库；缺少显式模式或错误准入模式不创建产物、不读取key；
初始化失败保留私有诊断；Ctrl-C期间终止真实sleep子进程、删除自有临时目录。
中断测试仅替代慢速fixture初始化，不替代被测CLI收尾逻辑。

RED记录：

- 新入口尚不存在时，帮助命令返回2，测试期望0而失败。
- 首轮3项集成检查通过后，只读审查发现unittest遇KeyboardInterrupt跳过类级清理。
  新测试先失败于`owned child must not survive Ctrl-C`；随后在CLI外层finally补幂等清理，
  独立中断/普通初始化失败检查2/2通过。RED测试自身finally清理了探测子进程，没有残留。

所有数据库验证在当前ECS串行执行：CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、TasksMax256、读写各5MB/s及200IOPS；nobody/nogroup、Nice10、
UMask0077、NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
仅允许localhost，不读真实key、不修改业务库/服务。低于512MiB可用内存时停止本轮单元。
`KillMode=control-group`仍是SIGKILL等不可捕获终止的进程兜底，不声称Python finally能捕获它。

复现本轮无付费测试：

```sh
PYTHONPATH=python:test/e2e \
SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m5_benchmark_cli_test.py
```

需放入上述限额单元。M5及P0仍未完成。

## 实际结果

[最终日志](verified-journal.txt)：**5/5检查通过，0失败、0跳过，3.986秒**；
其中只有1项执行完整Agent/SQL/模拟审核链路，另外4项是门禁、帮助和失败/中断清理检查。
外部供应商请求0次，替代网络请求1次；synthetic reviewer确认/执行1次，不是人工业务成绩。
另10条迁移PASS排除。开发首轮[3/3日志](initial-journal.txt)及独立RED/GREEN检查不累加分母。

[初轮实时配置](initial-running.txt)确认既有限额和localhost隔离；
最终轮使用相同systemd-run参数。最终轮结束较快，未赶上保存该单元的实时属性：
卸载后systemctl显示的默认UMask/Nice/Runtime等**不作为实际运行属性**，不为补截图重跑测试。
[终态日志](final-status.txt)确认00:11:44启动、00:13:33正常退出（Asia/Shanghai，含建库/迁移/清理），
两个本轮cgroup已移除，无nobody进程残留，原MySQL仍active；
memory.high累计80521未增加，max/oom/oom_kill均0，可用内存2042MiB。

[源文件与二进制哈希](sources.sha256)记录28个实际导入/夹具/包装器文件，
均与受限副本逐字节相同，另绑定server/admin，本轮未编译。
只读审查发现的Ctrl-C资源遗留问题已修复并复核，无剩余代码阻断项。
