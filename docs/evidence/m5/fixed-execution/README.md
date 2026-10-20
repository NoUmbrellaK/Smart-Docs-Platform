# M5：固定任务执行与采集连接层

2026-10-20执行、10-21整理，基线`9721db2`，本增量只增加测试侧适配器和验证，
不修改产品、schema、冻结任务或模型循环。
[benchmark_execute.py](../../../../test/e2e/benchmark_execute.py)将
`FixedInputsE2E.prepare_case`的新pending任务交给既有`agent_cli.run_context`一次，
随后在夹具仍存活时调用既有`capture/write_snapshot`。无新依赖。

## 使用边界

可信调用者传入case ID、prepare_case返回值、五字段metadata、新输出目录、与owner client一致的
backend_port、显式key、max_calls（1–8）及run_kind。本函数不读环境变量或密钥文件，
不自行取得外发/费用授权；`live_provider`只是调用者声明，不是运行证明。
模型固定目的地、输出预算和请求限制仍由既有DeepSeekModel/runner控制：
只读输出最多512 token，报告/归档结构化输出可能高于512（最高8192），
**未来付费评测必须按实际任务模式重新限定授权，不能套用此前6×512烟测许可。**

- 校验case创建问题、准入状态、端口一致、任务ID/项目/版本范围和pending状态；
  GET状态接口故意不返回问题正文。最终领取及权限判断仍由C++权威执行。
- 排他创建0700目录；会话context为0600临时文件，正常返回、普通异常和协程取消时清理。
  已有目录/软链接不复用，不自动覆盖；调用者须提供可信私有父目录。
- 普通任务保留0600安全审计与`snapshot.json`，后者包含实际结果、原文及映射，
  因而必须私有保存，**不要提交原始快照**。不序列化owner client，不把oracle或映射放入模型context。
- Q08/Q09/A09按固定403/404准入结果采集，不领取任务、无需key；本轮实际运行了Q08分支。
- 普通异常转为无异常正文的unknown结果，再尝试采集；不自动重跑模型、取消任务或重新执行写入。
  协程取消向上传播，强杀、磁盘故障、采集前置失败可能没有完整快照；缺失不能当作成功或允许重跑。
- 归档只到waiting_confirmation，不调用确认接口或resume。调用者负责保留服务/数据库及夹具，
  等待**另一次真实用户确认**。测试结束会按既有fixture清理；本适配器不是长期人工确认会话管理器。
- `capture.complete`只代表采集一致性，`execution.outcome`是安全状态，不代表业务正确；
  离线评审仍需人工判定，`execution_verified`仍为false。

没有新增批量付费CLI、30项业务结果、真实人工确认或语义评审。冻结任务仍为not_run。
后续[单案例CLI](../case-cli/README.md)已复用本连接层，提供有截止时间的网页审核现场；
不是无限期托管，也不改变本文本地协议验证的统计边界。

## 验证

[新测试](../../../../test/e2e/m5_benchmark_execute_test.py)通过真实Agent、stdio MCP、C++和
独立MySQL，只把供应商网络替换为HTTPX MockTransport。测试输出是协议夹具，不是模型成绩。
本轮不读真实key、不调用外网、不修改生产库或服务。

七项集成检查：Q01执行并采集固定原文、H01保存报告、A06待确认且文件不移动、
Q08拒绝不启动、503保留失败且不重试、max_calls=1不扩大、已有目录/错误case在领取前拒绝。
另一个无网络guard验证端口错配先于HTTP和产物创建被拒绝。
测试检查审计请求数、0600/0700权限、临时会话清理及产物无测试key/cookie。

在当前ECS的`/tmp/smartdocs-safe.h4SzvX/repo`，沿用
smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
TasksMax256、磁盘读写各5MB/s和200IOPS，nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
仅允许localhost；低于512MiB可用内存时停止本轮单元，不放宽资源限额。

```sh
PYTHONPATH=python:test/e2e \
SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m5_benchmark_execute_test.py
```

需放入上述systemd限额单元；包装器使用禁网络、32MiB buffer的临时MySQL socket，
迁移检查不计入8项分母。原server/admin复用受限构建，本轮不编译。

## 开发中保留的问题

1. 初次导入RED：`ModuleNotFoundError: No module named 'benchmark_execute'`，之后实现适配器。
2. 只读审查发现执行/采集端口未绑定。新guard先失败于继续访问HTTP，
   添加一致性检查后独立1/1通过。不会把这个独立检查重复计为最终分母的一项新增。
3. 首轮隔离链路[失败日志](initial-journal.txt)：7项中1通过、6个KeyError `question`，
   13.769秒；尚未到模型网络层。根因为GET状态接口不返回问题正文，已按正式契约修复。
   此次失败不是服务器故障，不删除或改写为通过。
4. 源码核对最初范围过宽：安全副本缺少未使用的embedding依赖清单，
   未执行的m1_browser_self_test.py也与工作树不同。没有覆盖这些文件或安装依赖；
   改为按实际导入依赖、固定JSON和包装器核对，不声称整个安全副本完全相同。
5. 第二轮[共享库断言失败日志](shared-db-journal.txt)：8项中6通过、2失败，16.140秒。
   五个实际Agent用例均通过；两项拒绝检查错误地断言整库agent_runs=0，
   但测试类共用隔离数据库，之前用例已有运行记录。改为比较操作前后总数，
   保留“不得创建新run”的行为检查，没有删除断言或修改产品。

## 最终结果

[最终日志](verified-journal.txt)：**8/8通过，0失败、0跳过，16.331秒**。
这包括7项真实HTTP/SQL链路检查和1项无网络guard；另10条迁移PASS排除，
两次失败整组运行、独立guard复验也不累加为新任务或更大的成功分母。
五个实际Agent用例合计6次**替代网络响应**（2+1+1+1+1），不是真实供应商请求或实际usage。

[实际运行配置](running.txt)记录23:57:38（Asia/Shanghai）启动，最后正常退出并清理临时库/存储；
[终态](final-status.txt)记录inactive/dead、三个本轮cgroup均已移除、无nobody残留进程、
原MySQL仍active。单元已卸载，终态时间戳为n/a，不据此伪造完整墙钟耗时。
终态可用内存2024MiB，memory.high累计80521未变，max/oom/oom_kill均0；
离散观察不代表峰值RSS或长期稳定性。

[源码与二进制哈希](sources.sha256)绑定26个实际导入模块/固定JSON/包装器文件，
均与工作树逐字节一致，另绑定server/admin。测试前后二进制哈希相同，
不把旧浏览器脚本或未使用的依赖清单包含进一致性声明。
只读审查的端口绑定问题已修复，复核无新增重要问题。

M5、真实30项任务、全部实际引用评审、部署验证和完整业务演示仍未完成。
