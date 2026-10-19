# A20 供应商失败原因贯穿持久任务结果

2026-10-19；产品基线`99b9565e78ce6a049c8f48c60da7ec78ae0770d7`。
对应需求A-01/T-19的明确失败原因；不是M3/P0验收、真实供应商测试或性能测量。

## 缺口与修复

原适配器把非200均抛为dependency_failed，只在本地轨迹保留provider_reason；CLI安全摘要
和C++ FinishRun也不接受这些具体码。用户查看任务只看到依赖失败，无法区分限流、配置或余额问题。

复用现有错误码/finish_code/任务结果页面：HTTP401→provider_auth、402→provider_balance、
429→provider_rate_limit、5xx→provider_unavailable，其余非200（含重定向）→provider_rejected。
只传播固定枚举，不返回供应商报错正文、key、会话或文档。未知错误码继续拒绝/脱敏；
不增加自动重试、供应商切换、额外工具调用或数据库迁移。网页实现未修改，Reason字段已有展示能力。

## RED与审查

- [unit-red.txt](./unit-red.txt)：2个测试方法，10个子断言失败，分别复现适配器及安全摘要丢失5个码。
- [http-red.txt](./http-red.txt)：2个方法，5个供应商分类断言失败；2个新增finish码被C++400拒绝。
  随后3个子场景因先前失败留下running任务而触发2槽限制，这是夹具级联，不伪称3次分类复现。
  已在直接finish测试的每个子场景finally中取消未完成任务，避免后续诊断污染。
- [review.md](./review.md)：只读审查未发现阻断问题，不代替运行验证。
- [build.txt](./build.txt)：原限额下make -j1 server admin bin/smartdocs_tests增量构建成功；不运行clean。
- [initial-regression.txt](./initial-regression.txt)：协议回归88/88通过后，脚本误用系统/usr/bin/node，
  其不支持--test，单元退出9；当次未运行后续前端/Python/SQL测试。已改为既有受限副本的
  Node v24.18.0并重新执行完整脚本，不安装或升级系统依赖，也不删除原失败记录。

## 本轮实际验证

[regression.txt](./regression.txt)记录以下实际通过结果（均0失败、0跳过）：

| 阶段 | 实际结果 |
| --- | --- |
| MCP/执行器 | 88/88，70.160秒 |
| 前端模块 | 101/101 |
| 页面契约 | 8/8，0.066秒 |
| 本地Python | 106/106，21.884秒 |
| 相关C++，filter=m3_ | 17/17 |
| Agent真实HTTP/SQL | 5/5，14.947秒；其中新增2个方法各含5个固定子场景，不另算10个方法 |

该串行单元随后在矩阵setUpClass创建固定管理员时退出1：两个独立E2E入口共用了临时库，
前一入口并不删除worker-e2e-admin，后一入口重复create-user退出2，矩阵当次运行0组。
这不是25组通过，也不删除该失败。复现脚本已拆成两个独立临时库；仅重新执行矩阵，
不会重复统计前面的88/101/8/106/17/5组。

[独立矩阵复验](./matrix.txt)：25/25，31.844秒，0失败、0跳过；对应A19固定分母，
不新增或合并计数。其中15组stdio→真实C++/SQL，5组stdio参数拒绝，5组in-process SDK
发送前超时注入；不作为25组真实网络故障、Agent任务完成率或引用准确率。
[最终资源](./resources.txt)：所有本轮单元MainPID=0，slice TasksCurrent=0；结束时
MemAvailable=1213MiB，原MySQL仍active。memory.events的high保持80521，max/oom/oom_kill=0，
无新增节流/OOM；构建与独立矩阵单元success，其余单元的RED/环境/夹具退出状态保留如上。
[哈希](./sha256.txt)记录最终源码、测试及实际三个二进制；10个相关源码/测试文件与隔离副本逐字相同。
各阶段在同一产品版本通过，不宣称有一次无中断的全套脚本运行；修正后的verify.sh汇总复现步骤。

新增真实HTTP方法包含5种固定供应商响应，外部网络仅由HTTPX MockTransport替代；
同源入口、CLI、stdio MCP初始化、C++和SQL均为真实组件。本组供应商首次请求即拒绝，
不声称这些场景执行了MCP业务工具：断言agent_tool_calls、archive_plans、handover_reports均为0。
各任务仅1次供应商模拟请求，失败持久落库、重复execute返回409；usage未知仍为null。
直接finish方法各验证未知码400、合法失败提交成功、同值重放成功、异值409。
既有页面模块新增测试检查5种码都显示、没有Start按钮且不展示未知body字段；未修改布局或重跑浏览器截图。

## 复现及运行边界

完整串行验证命令见[verify.sh](./verify.sh)，其中临时环境路径对应本轮既有受限副本。
执行环境为nobody/nogroup、Nice10、UMask0077、NoNewPrivileges，单元上限900秒，
smartdocsvectorcheck.slice CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、TasksMax256，
/dev/vda读写各5MB/s及200IOPS。可用内存低于512MiB停止本轮单元；不提高限额。
隔离MySQL只监听私有socket，32MiB缓冲池，关闭performance_schema，包装器清理自身临时库。

部署时配套更新C++及Python，仍要求schema16；旧C++拒绝新增失败码，单独更新Python可能留下running。
历史dependency_failed不回填为猜测原因。原业务库未迁移，生产服务未替换，真实key未读取。
MockTransport失败分类不证明远程账号/配额/模型可用，也不证明实际token费用、真实动态决策、
30项业务任务或50条引用准确率；这些验收仍待后续。
