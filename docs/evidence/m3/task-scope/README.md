# M3 持久任务与固定版本范围

2026-10-18，北京时间。代码基线95e452c4921533f28fe15c37b2748ea2ad48c152，
设计/计划提交4e25257，分支codex/m3-mcp-agent。本增量没有模型调用、新依赖
或生产部署；原业务库未升级。接口与迁移边界见[说明](../../../api/m3-agent-tasks.md)。

## 实现范围

C++持久保存本人任务、问题、用户外发确认和1–100个固定文件版本；每次工具调用
事务内复核项目权限及全部选中版本，搜索候选、计数和返回均受任务范围约束。
取消与在途读取串行，取消提交后新调用拒绝；重启将running标为interrupted。
全局最多100个pending/running、2个running，不把工具开始误称为模型已运行。

MCP新增可信context.scope=task_versions，两个现有工具映射到固定任务路由，
失败不降级到全项目接口。旧project_approved_versions仅保留本机调试用途。
当前没有模型执行器、完整调用/token预算、调用日志、结果终态或任务网页。

## RED与审查

- [C++ RED](cpp-red-journal.txt)：实现前新增4项均因任务路由缺失失败。
  后来新增的第5项是独立审查要求的取消并发回归，不将其记入该RED分母。
  测试中撤销批准的直接SQL在GREEN前同时清空批准时间/人，以满足已有数据库约束；
  RED停在缺失路由处，并未执行到该语句。
- [MCP首次RED](mcp-red-journal.txt)：11项中1项因尚不支持task_versions出错；
  [显式断言RED](mcp-red-assert-journal.txt)把同一缺失能力转换为1项断言失败。
  两次记录保留，不累计分母。
- [MCP GREEN](mcp-green-journal.txt)：真实官方SDK客户端/本地HTTP接收器11/11，
  21.338秒；包含固定任务路由、无身份参数、409后不降级、分页和超时边界。
  HTTP接收器不是C++业务权限证明，真实链路单独记录。
- [构建](build-journal.txt)：make -j1构建smartdocs_tests、server、smartdocs-admin；
  后续并发回归测试源码另随首组重编译，生产代码未再变化。
- [独立只读审查](review.md)：最终无Critical/Important；补取消在途读取测试及
  异步启动异常清理。200ms等待不是数据库锁等待表证明，不宣称覆盖所有调度时序。

## C++回归与迁移

[首组80/80](cpp-group-1-journal.txt)、[次组75/75](cpp-group-2-journal.txt)，
合计155个唯一用例、0失败、0跳过。两份selection清单并集与当前test/unit及
test/integration的155个注册名称完全一致、无重复，也无完整名称子串歧义。
每项用例独立进程，并非一次进程内连续执行155项。首组包括5项新任务用例：
范围内检索/读取、真实确认/本人隔离、整范围失效、容量/重启、在途读取/取消。

两组均经过隔离库升级到schema6、迁移重放及部分迁移拒绝检查，保留103份原有
文件且不伪造解析结果；新表初始为0任务、0范围、1守卫，不虚构用户确认。
这两项脚本PASS不计入155项C++分母。实际增量源码、迁移、测试和三个二进制见
[SHA-256](source-and-binary-sha256.txt)。

## Python、真实链路与终态

[联合日志](python-e2e-journal.txt)：既有Python104/104（21.986秒）；真实SDK stdio
→C++ socket→隔离MySQL→真实解析worker共3/3（6.453秒，不含建库）。新增场景
同时批准两份资料但只选择一份，确认范围外名称/计数不返回、范围外读取拒绝，
同一MCP客户端在取消提交后拒读；第二任务在C++重启后为interrupted，已取消任务
仍为cancelled。原两场景继续验证历史引用/版本批准及13片段Markdown与中文PDF页范围。
没有远程模型、真实向量模型或新的生产部署验证，不将接收器测试冒充业务E2E。

[最终核对](final-status.txt)：全部本轮单元MainPID=0；RED保留exit1，其余exit0。
原MySQL仍为PID975、active/running；17份增量源码/迁移/测试与执行副本cmp一致，
全部20项源码/实际二进制SHA检查通过，主工作区无改动。
memory.events离散采样high由80269到80272，增加3次节流；max/oom/oom_kill保持0。
不把无OOM表述为无内存压力，离散采样不是连续峰值或长期稳定性证明。未读取
DeepSeek密钥、未产生API token费用。测试临时数据库和场景资料已由夹具自动清理。

## 复现与资源限制

当前ECS，执行副本/tmp/smartdocs-safe.h4SzvX/repo。串行systemd临时单元，
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges=yes、TimeoutStopSec5、
KillMode=control-group；协议单元120秒，其余900秒。smartdocsvectorcheck.slice：
MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、CPU100%、Tasks256，/dev/vda读写
各5MB/s、200IOPS；可用内存采样低于512MiB即停止。不放宽配额、不make clean。

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
# 两组依次运行，每个完整名称一个新进程，遇失败立即退出。
for selection in cpp-group-1-selection.txt cpp-group-2-selection.txt; do
  bash scripts/test-with-mysql.sh -- bash -c '
    while IFS= read -r test_case; do
      stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case" || exit $?
    done < "$1"' _ "docs/evidence/m3/task-scope/$selection" || exit $?
done
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
PYTHONPATH=python SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py
```

临时单元名为smartdocs-m3-task-{red,mcp-red,mcp-red-assert,mcp-green,build,
group1,group2,python-e2e}。解释器路径是本机已有独立环境，复现时替换为自己的环境；
MCP SDK环境不含pypdf，PDF worker通过SMARTDOCS_TEST_PARSER_PYTHON选择解析环境。
隔离数据库和场景临时资料由夹具清理，不可恢复，不涉及原业务数据。

## 后续门槛

schema6只新增任务表，实际部署需停服务、备份并迁移，不能直接替换schema5上的
二进制；MySQL DDL非整批原子事务，半完成迁移应恢复备份处理。

本层检查当前授权状态，不持久化范围失效事件。未来模型循环必须在范围冲突后
终止该次执行并清空/重建上下文，还需在每次外发前复核；恢复批准不意味着旧模型
上下文可继续复用。创建也尚非幂等接口，不可盲目自动重试。
生产内部HTTP、模型出口、多步动态执行、完整预算/日志/结果、查询向量接线、
三个写工具与M4归档/报告仍待实现；不宣称完整M3或T-18模型出口验收通过。
