# A30：固定任务的未领取装载入口

2026-10-19；基线 `85ff236` 加本提交，仅修改既有
[m3_fixed_inputs_test.py](../../../../test/e2e/m3_fixed_inputs_test.py) 和说明，不改变产品代码、schema、
模型提示词或两份冻结JSON。复用原装载逻辑，没有新建测试服务或并行任务框架。

## 改动和边界

旧 `preflight()` 会创建任务后领取、遍历资料并取消，不能作为真实Agent的初始任务。
现抽出 `prepare_case(case)`：仍通过真实HTTP装载文件、版本、权限、目录和清单，
完成新版本批准与原文字节/解析状态核对后，只创建pending任务并返回实际owner客户端和任务路径。
具体返回字段及使用边界见[夹具说明](../../../../test/fixtures/agent-p0/README.md)。

`preflight()` 继续调用该入口，再完成原来的领取、搜索分页和取消；默认CLI行为不变。
新增 `--pending-only` 检查全部30项装载在返回时未领取，不能替代旧预检，也不能算业务运行。
两种模式分别用新隔离库，不能复用预检任务或来源台账。

- 成功创建的27项必须保持pending；Q08/Q09仍404，A09仍403且没有任务。
- 逐项SQL核对整个隔离库的 agent_runs、agent_tool_calls、archive_plans、archive_decisions、
  handover_reports 均为0；没有执行run/start、任务工具搜索/读取或模型来源准入。
- 装载者在任务创建前仍核对获批原文，这不是Agent阅读，也不产生该任务的工具/模型记录。
  H03新版未解析、无chunk；H05保留声明顺序及11份版本，不预先替Agent遍历任务分页。
- 返回的actor携带私有Cookie，不能打印、序列化或发给模型；固定expected、mapping也不是模型上下文。
- 返回值只在同一次测试生命周期内有效；cleanup仍取消未完成任务，结束后没有保留待执行池。
  后续真实执行驱动须重新装载，不能把此处检查通过当成实际模型动态选择或归档确认。

## 实测证据

- [RED](./red.txt)：Q03新增检查 **0/1**，0.282秒，明确因prepare_case尚不存在失败。
  不修改产品或数据库来制造失败；单元退出1的记录保留。
- [新增装载模式](./green.txt)：**30/30**，66.492秒，无失败/跳过；23:34:04–23:36:55北京时间，
  含隔离库初始化/迁移/清理的外层时间不计入66.492秒。日志逐项保留business_task=not_run和零计数。
- [原预检回归](./regression.txt)：**30/30**，71.182秒，无失败/跳过；23:37:40–23:40:34北京时间，
  保留Q03首次查询空结果、H05真实10+1分页、清单/目录上下文及取消检查。两种模式是同一批30项
  的不同前置检查，不能相加宣称60项业务任务通过。
- 冻结夹具静态检查 **4/4**，0.003秒；不计为业务任务。只读审查未发现重要问题，
  明确指出返回值仅在当前生命周期有效，已补充文档。

真实供应商调用数为0，没有语义任务结果或实际引用准确率。M3及整个P0仍未验收。

## 复现与约束

使用既有受限副本 `/tmp/smartdocs-safe.h4SzvX/repo`，仅改变包装器最后的测试命令：

```sh
# 已设置PYTHONPATH=受限副本/python:受限副本/test/e2e，以及既有parser Python路径
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B \
  test/e2e/m3_fixed_inputs_test.py --pending-only
# 原预检必须使用另一个新隔离库；不是在上述库继续执行
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B \
  test/e2e/m3_fixed_inputs_test.py
```

两条命令分别在 `smartdocsvectorcheck.slice` 的串行systemd单元执行：uid=nobody/gid=nogroup、
Nice10、UMask0077、NoNewPrivileges、KillMode=control-group、TimeoutStopSec5、RuntimeMaxSec600；
IPAddressDeny=any且只允许localhost。RED使用同配置、RuntimeMaxSec300、只选test_pending_Q03。
共享限额保持单核CPU、MemoryHigh1200MiB/Max1600MiB、Swap0、磁盘读写各5MB/s和200IOPS；
只监听私有socket的测试MySQL使用32MiB缓冲池、关闭performance_schema，不重编译或改业务库。
复现须先准备同版本依赖/二进制和副本、选择未占用单元名，不并行执行或提高资源限额。

[最终环境和哈希](./environment.txt)：三单元MainPID均为0，slice TasksCurrent=0，
原mysql仍active，MemAvailable1228MiB；memory.events的high保持80521，max/oom/oom_kill保持0，
无新增内存节流/OOM。修改脚本与被测副本cmp一致，两份冻结输入哈希未变，提交前差异格式检查通过。
wrapper已清理三次独立测试的临时库和存储；这些合成资料不可恢复，可按脚本重新生成。
没有删除业务文件、读取API key、调用供应商、更新业务数据库或部署生产服务。

## 本轮停止及限定测试授权

用户在本轮明确批准了此前询问的一次限定DeepSeek测试：使用已保存的key，仅发送人工公开的
ORBIT-27部署说明、测试问题、系统提示和工具定义，最多6次请求、每次最多512输出token，
允许少量费用，不发送真实业务资料或用户会话。**随后用户要求完成当前任务后停止。**

因此本轮只完成当前装载器修改及其必要回归，不启动该供应商测试，也不读取key。
该测试处于“本对话已批准、尚未执行”而非“仍缺此项批准”；批准不扩展至30项评测或其他资料。
停止要求生效后不自动开始下一任务，等待用户重新要求继续；这不是整个长期目标完成。
