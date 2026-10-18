# M3 有界只读执行核心验证

2026-10-18，基线fe91a5a，设计提交1bfa3c2。接口与边界见
[执行核心](../../../api/m3-read-agent.md)，审查过程见[review.md](review.md)。
这是M3增量，不是完整M3、T-10/T-18或P0验收。

## 交付范围

C++ schema7持久运行记录、排他领取、外发前整范围复核、8次调用预算、180秒期限、
结果来源校验、幂等终态及失效清除；Python复用现有两个只读MCP工具，根据实际
中间结果继续搜索/读取，限制12次工具、每轮64KiB/累计512KiB、模型60秒及1024输出token请求。
最后完成响应不确定时仅重放同一结果一次，再查询权威结果；不虚构终态。
首次明确invalid_source则提交failed/null。180秒执行后协调最多额外30秒。

只用本地脚本化供应商，不读取DeepSeek密钥，不发生真实模型调用或计费；没有新增依赖。
没有生产入口、逐调用持久审计、网页、三个写工具，也没有迁移原业务库。

## 实测结果

- [构建](rebuild-journal.txt)：make -j1构建测试、server、admin三个实际二进制成功。
  [二进制SHA](binary-sha256.txt)记录本轮实际产物。
- 相关C++ **69个唯一用例全部取得通过结果**，不是全仓158项全量复验。
  [选择清单](cpp-selection.txt)涵盖processing_jobs、auth_project、mysql_schema、
  mysql_readiness四个测试文件，完整名称没有子串碰撞。
  [清单审计](selection-audit.json)确认69个源码用例与69条唯一通过记录一致，无漏项。
  [第一单元](cpp-timed-journal.txt)通过67项后达到900秒上限，终态timeout/9，
  **不能称该单元成功**；[剩余2项](cpp-remaining-selection.txt)在
  [新单元](db-final-journal.txt)通过且单元正常退出。未放宽资源或断言。
- [最终Python日志](python-final-journal.txt)：MCP/执行器 **23/23**（27.612秒），
  既有Python **104/104**（20.815秒），无失败或跳过。
  未知写工具负例仍会输出SDK的“not listed”诊断，实际拒绝及未调用后端断言通过。
- [真实SDK stdio→C++→隔离MySQL链路](db-final-journal.txt) **5/5**（7.385秒）：
  空搜索→调整查询→读固定片段→保存引用回答；真实文件重启后结果保留；
  撤销批准阻止下一次模型请求；既有任务取消/重启、13片段文本拼回、中文PDF物理页及权限。
  模型是本地替身，不能据此宣称真实模型质量或业务准确率。
- 隔离迁移保留103文件，不伪造任务、用户确认或执行结果，重复迁移通过。
  schema7只在隔离库验证，生产升级须停服务、备份并按手册迁移。

## RED与中途失败

[C++ RED](cpp-red-journal.txt)为3项缺少运行路由；
[Python RED](python-red-journal.txt)为7项缺少执行模块断言。
[第一次Python失败](python-first-green-failure.txt)是MCP异步上下文包装ValueError，
将断言放回上下文内部后继续验证；[第一次C++失败](cpp-first-green-failure.txt)
记录SQL夹具原件不存在导致重启不可用，以及失效领取缺少运行记录。
前者改用真实上传E2E验证持久性，后者修正事务记录顺序。

[丢失完成响应RED](python-review-red-journal.txt)、
[线程接收器竞态RED](python-race-red-journal.txt)、
[明确来源拒绝RED](python-source-red-journal.txt)均在对应修复前真实失败，
之后纳入最终23项回归通过。[中间21项通过](python-mcp-green-journal.txt)只代表当时版本。
原始失败记录不删除；没有用跳过、放宽限额或伪造授权消除失败。

## 复现与资源

执行副本`/tmp/smartdocs-safe.h4SzvX/repo`；[变更源文件SHA](source-sha256.txt)
逐项与当前工作区一致。复用已有解析与MCP虚拟环境，SDK1.30.0；MCP环境不含pypdf，
E2E通过SMARTDOCS_TEST_PARSER_PYTHON指定解析环境。

实际命令均由独立systemd临时单元串行执行，不在业务进程中运行：

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
# 第一轮选择全部69项；900秒后只对未完成2项用新单元继续。
bash scripts/test-with-mysql.sh -- bash -c '
  while IFS= read -r test_case; do
    stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case" || exit $?
  done < cpp-run-selection.txt'
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
PYTHONPATH=python SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  bash scripts/test-with-mysql.sh -- bash -c '
    set -e
    while IFS= read -r test_case; do
      stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case"
    done < cpp-run-remaining-selection.txt
    /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py'
```

两个cpp-run选择文件分别是本目录清单的原样副本。以后复现宜将69项预先拆成
更小分组，避免初始化/迁移/磁盘节流占用900秒预算；不要延长总限额。
临时单元前缀smartdocs-m3-run-；运行参数nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges=yes、RuntimeMaxSec900（初始Python RED为120）、TimeoutStopSec5、
KillMode=control-group。共用smartdocsvectorcheck.slice：MemoryHigh1200MiB、
MemoryMax1600MiB、Swap0、CPU100%、Tasks256，/dev/vda读写各5MB/s、200IOPS。
采样可用内存低于512MiB即停止；本轮采样未触发。

[最终状态](final-status.txt)：所有单元无存活主进程；成功临时单元已被systemd回收，
show的默认/infinity值不是实际运行参数，成功终态以journal的Deactivated successfully为准。
memory.events从high80272/max0/oom0/oom_kill0到相同值：无新增节流、OOM或OOM kill。
原MySQL始终PID975，最终无隔离MySQL目录。超时残留目录smartdocs-mysql.jh8EKq
已在原限额下清理，其余测试目录由夹具清理；均为临时资料，不可恢复，无业务数据删除。

## 后续

接入受控的真实供应商传输及脱敏usage审计、小额公开资料验证，再做生产内部MCP认证、
任务网页与M4写业务。当前用户会话运行API不是防会话持有者伪造执行的证明，
结果仅为来源受控的草稿；观察到范围失效永久清除，但不监听全部历史策略事件。
