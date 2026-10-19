# A15 受控同源 Agent 执行入口

2026-10-19；基线 `e8c0740801f2dfd8a0e4274759b3f3975dfcb936`，分支
`codex/m3-mcp-agent`。被测源码与既有二进制见[SHA256](sha256.txt)；本增量未改
C++/SQL/网页，不重建后端。接口及未安装部署模板见[契约](../../../api/m3-agent-http.md)。

## 最终实际结果

[组合日志](final-green.txt)：MCP/执行器 **87/87**，既有Python **106/106**，
真实HTTP→stdio MCP→C++→临时MySQL **3/3**，均无失败/跳过。
MCP套件中的安全测试单独启动live测试发现，确认其默认跳过1项且不启动数据库；
这不是遗漏的真实模型成功记录。HTTPX只替换付费网络，执行器、子进程及业务后端均真实运行。

- 新增入口单测10项：固定路由/同源/唯一会话、重复头及非法JSON拒绝、禁止客户端
  提供身份或模型参数、非pending/后端错误、私有context清理、未知异常不泄漏、
  后置鉴权、两个连接槽、受保护目录，以及活动请求关闭等待/SIGTERM正常退出。
  另[独立验证](entry-search-green.txt)入口10/10及既有检索HTTP10/10。
- 真实链路1：reader本人任务，其他管理员404；未命中关键词→调整查询→真实片段
  固定引用→持久结果，三次合成模型响应；重复执行409，批准撤销后无新增模型请求。
- 真实链路2：请求执行中另一请求409；真实C++取消后下一步被拦截，只有一次
  合成模型请求和一条运行记录，没有重复任务/执行。
- 真实链路3：可信清单及服务端操作ID生成uncertain报告；可信归档上下文保存方案
  后停在waiting_confirmation，文件位置不变，没有真实确认或移动。两个任务各一次
  合成模型响应，提案模型没有execute工具；重复执行409。审计无合成密钥/会话/内容标记。
- 临时库迁移包装器同时检查schema15、重复迁移、拒绝半套DDL和103文件升级保留，
  不代表对原业务数据库执行了迁移。

## 失败过程与修复

1. [入口缺失RED](missing-entry-red.txt)：最初8项均因模块不存在失败。
2. [首次实现7/8](unit-fixture-failure.txt)：UMask0077把测试mkdir(0755)变成0700，
   无法证明不安全目录被拒绝，并产生未关闭测试socket警告；改为显式chmod0755。
3. [执行异常RED](execution-error-red.txt)：ValueError发生在运行中也被标400；
   限缩解析异常映射后改为503/unknown，临时context仍清理，无自动POST重试。
4. [关闭RED](shutdown-red.txt)：10项中2项失败，活动请求未等待、SIGTERM退出-15。
   非daemon请求线程及主线程之外的shutdown修复后10/10通过；[只读复核](review.md)通过。
5. [首轮联调2/3](integration-first-failure.txt)和[定位复验2/3](integration-fixture-diagnosis.txt)：
   模拟模型误以为 `absent-keyword-192837` 必然无结果；实际ngram分词与SOURCE-MARKER
   有共享片段，导致夹具断言被执行器归类model_invalid。替换为无共享分词的zzzzzz，
   不改产品检索规则，最终3/3通过。定位日志仅含合成资料及一次性测试ID。

真实链路三轮合计7项通过/2项失败，最终轮3/3；不将失败轮删除或算成首次全绿。

## 复现与资源

当前ECS必须使用原受限测试组，不直接运行无限额完整验收。源码按文件安装到
独立副本 `/tmp/smartdocs-safe.h4SzvX/repo`，4个变更Python文件与工作树cmp一致；
User=nobody、UMask0077、单核CPU、MemoryHigh1200MiB/Max1600MiB、无swap，
磁盘读写各5MB/s和200IOPS，TasksMax256；同一时间仅一个串行测试单元。

最终单元 `smartdocs-agent-http-final` 使用RuntimeMaxSec900、KillMode=control-group
（这是测试超时清理，不是生产模板），PYTHONPATH指向副本python，
SMARTDOCS_TEST_PARSER_PYTHON指向已有parser环境，依次执行：

```sh
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_agent_http_test.py
```

临时MySQL仅私有socket、32MiB缓冲池；空root密码提示只涉及隔离库。
测试夹具清理合成上传、临时数据库、context与临时审计，不涉及用户业务资料。
[终态](resources.txt)全部测试MainPID=0、共享组TasksCurrent=0，原MySQL仍active。
memory.events从本轮开始到结束均high80521/max0/oom0/oom_kill0，无新增节流/OOM；
采样可用内存均高于512MiB停止阈值，不宣称测量了峰值或长期稳定性。

## 限制与下一步

没有读取真实API key、付费请求、新依赖、生产迁移或常驻部署。没有运行全仓C++、
浏览器、真实供应商效果或Nginx/systemd生产安装验收。此网关是同源代理后的私有
适配器，不是公网MCP服务/自动恢复队列；强制终止后的running需查状态并显式处理，
不能仅重启网关就宣称恢复。下一增量为网页显式执行按钮及未知结果查询；目录管理、
完整模型审计/固定任务评测和演示另行推进，M3及整个P0尚未验收。
