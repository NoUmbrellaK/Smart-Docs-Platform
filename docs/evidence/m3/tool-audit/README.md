# M3 后端工具请求轨迹

2026-10-18至19日，基线`9ffccb7f6b03aee28cb87df1f4323a4349846f0e`。
契约见[后端轨迹API](../../../api/m3-tool-audit.md)。C++记录实际任务搜索/读取请求，
不是模型自报结果；只有查询接口，没有客户端写入接口。固定64条上限，鉴权查询
重验本人任务、项目和整个当前版本范围；参数摘要不含原查询、正文或文件名。
此增量不含网页摘要、不调用模型、不迁移原业务库。

## 失败证据及修正

1. [初始RED](initial-red.txt)：既有路由4/4通过，新3场景均因缺失接口404失败。
2. 首次实现构建成功，但[真实HTTP首轮](first-http-failure.txt)在第65次请求退出：
   前64次并发请求成功，达到上限后本应429，却因公共HTTP ReasonPhrase遗漏429，
   构造错误响应时抛异常，导致隔离C++进程退出。之后连接拒绝为级联现象。
   这不是OOM，生产MySQL未受影响。最小响应单元测试独立复现unsupported HTTP
   response status（[定向RED](http-429-red.txt)），修复公共响应层，不能改返回503掩盖容量语义。
3. 独立只读审查指出结束记录写入失败时，全局错误处理会生成新request_id，无法
   关联原unknown行。故障测试使用真实MySQL触发器拒绝INSERT或UPDATE；UPDATE
   路径改用真实chunk读取并验证无data/无原文且错误request_id对应unknown记录。
   [定向RED](correlation-red.txt)复现ID不一致；另保持错误响应Connection: close，
   不更改成功API的data形状。[修订后只读复审](review.md)无阻塞项。

## 验证结果

[最终构建](reviewed-build.txt)三个目标成功，HTTP响应单元 **4/4**。
同名过滤器另外命中一项需要数据库的既有上传集成测试，该次 **跳过1项**，不计为通过。
本轮不声称完成全仓C++或浏览器复验。

[联合首轮](combined-first-run.txt)：新审计真实HTTP **4/4**、M3 C++ **17/17**、
数据库约束 **6/6**，均0失败/0跳过；迁移/重放及保留103文件检查通过。
随后MCP真实链路 **4/5**，PDF worker失败，单元退出1；后续MCP单测与哈希命令未执行。
排查发现本次命令漏传SMARTDOCS_TEST_PARSER_PYTHON，MCP解释器的pypdf模块不存在，
既有解析解释器有pypdf；测试默认回退sys.executable导致选错环境。仅修正测试环境，
不改变业务代码或安装依赖，后续独立重跑MCP部分；不把首轮宣称为整组GREEN。

[修正环境后复验](mcp-recheck.txt)：真实SDK/C++/隔离库MCP **5/5**、MCP/执行器/
供应商替身单测 **43/43**，0失败/0跳过，单元正常退出0。PDF页码与13片段Markdown
重建通过；供应商为本地替身，不是DeepSeek真实调用。

[源码哈希](source-sha256.txt)涵盖本轮所有生产代码、迁移和测试改动，已逐文件cmp
确认与受限执行副本一致。[二进制哈希](binary-sha256.txt)来自最终复验的实际产物。
[最终状态](final-status.txt)：本轮无运行测试进程，成功单元已回收，成功终态见构建/
复验journal；失败单元保留exit1记录。memory.events从high80272/max0/oom0/oom_kill0
到high80521/max0/oom0/oom_kill0：新增249次memory.high节流，无OOM/OOM kill，
不能宣称没有内存压力。原MySQL仍PID975，临时隔离库由夹具清理，无业务数据删除。

## 复现方法

所有命令在`/tmp/smartdocs-safe.h4SzvX/repo`的非root受限单元中运行；不要在宿主
无约束执行完整验收。源码与被测副本核对，二进制另记录哈希。

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
PYTHONPATH=python bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_tool_audit_test.py
PYTHONPATH=python bash scripts/test-with-mysql.sh -- bash -c '
  bin/smartdocs_tests --filter=m3_ && bin/smartdocs_tests --filter=mysql_schema_'
PYTHONPATH=python SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  bash scripts/test-with-mysql.sh -- bash -c '
    /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py &&
    /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p "test_*.py" -v'
```

日志中的联合首轮将HTTP、C++、MCP串在同一隔离库，C++重置库使MCP管理员可重新
创建；若拆分运行，按上述命令各用新隔离库，勿直接在同库反复创建同名测试管理员。

固定`smartdocsvectorcheck.slice`，MemoryHigh=1200MiB、MemoryMax=1600MiB、
MemorySwapMax=0、CPUQuota=100%、TasksMax=256、/dev/vda读写各5MB/s与200IOPS。
User=nobody、Group=nogroup、Nice=10、UMask=0077、NoNewPrivileges=yes、
TimeoutStopSec=5、KillMode=control-group、RuntimeMaxSec=900。重型测试串行；
主机可用内存低于512MiB停止。禁止清缓存、并行构建或放宽限额。

## 范围与限制

测试调用真实HTTP、C++、临时MySQL和本地TXT解析；不加载编码模型、不读取密钥、
不请求DeepSeek或外部服务。unknown重启用例将已存行改成仅开始记录的数据库夹具，
证明此状态可持久查询，不是实际进程崩溃窗口/落盘故障全覆盖。真实触发器拒绝写
覆盖准入失败及结束记录失败，但不模拟所有磁盘/连接中断情形。

schema8为新增表，不回填历史。部署需停服务、备份及显式迁移；初始化空密码提示
仅属于skip-networking临时测试库。当前业务库未升级，不能直接替换新二进制上线。
本表是后端请求数，不是MCP工具数：hybrid预检/最终查询可能各有一行。MCP校验失败、
模型usage与完整耗时仍由可信CLI本地轨迹记录。五工具、真实模型验证、生产执行入口、
归档与报告以及M3/P0验收仍未完成。
