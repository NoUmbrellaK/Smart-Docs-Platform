# HTTP 满载关闭竞态修复

日期：2026-11-06；基线 `b2a598fdc6c165c5cc11225113d1534351d0d38d`。
本增量对应需求第10节的有界并发/繁忙反馈，不是完整M5或P0验收。

## 根因与修改

上轮[首次离线失败](../report-wording-20261106/initial-offline.txt)发生在
`http.client`发送POST正文时。Agent与Search共同使用`SearchHTTPServer.process_request`：
两槽占满时立即503再完全关闭，未排空仍在途的请求头/正文，客户端可能收到BrokenPipe而非HTTP结果。
这不是模型失败或OOM；上轮复跑通过没有消除该竞态。

新增真实TCP用例先用MSG_PEEK等到早期503，再分两次发送正文（第二次故意延迟20毫秒）；
不是靠不断重跑碰运气，也不把BrokenPipe吞掉当成功。
未改产品代码时Agent/Search两例均失败，堆栈均在正文sendall，见[RED](red.txt)。
首次命令错误地以`test.mcp`/`test.python`导入，得到2个ImportError，
保留[命令错误](import-error.txt)，不计作RED。确认该单元终态后修正PYTHONPATH再执行。

产品仅在共享拒绝路径增加分阶段关闭：发送503 → SHUT_WR → 丢弃输入直到EOF或
总计250毫秒/64 KiB边界（每次recv不超过4 KiB）→完全关闭。
不解析被拒输入，不接触后端、模型、上下文目录；不新增业务线程，仍为两路业务并发。
采用标准库，未增加依赖、协议解析器或拒绝线程池；参考
[RFC 9112 §9.6](https://www.rfc-editor.org/rfc/rfc9112.html#section-9.6)的TCP分阶段关闭说明。

时间预算包含响应发送，trickle读取不重置截止期。串行收尾可能使下次accept延迟约250毫秒；
超过收尾时间/字节上限仍可能断连，不保证任意慢客户端收到503，也不是公网抗洪泛方案。
现有回环监听、代理限流要求、准入鉴权及任务确认/幂等规则不变。

## 实际验证

均在当前服务器，nobody:nogroup、禁外网/只准localhost，沿用原限额；没有读取API key、
没有供应商请求、没有真实归档、没有修改生产服务。
[单元采集](units.txt)保留两个失败单元的实际命令与配置；三个成功瞬态单元已被systemd回收，
其inactive/空配置/默认值不是运行时配置证据，见[加载状态](unit-load-state.txt)。
成功执行的配置以所附实际启动命令为准，终态依据journal的OK与Deactivated successfully，
不能只凭已回收单元的默认ExecMainStatus=0宣称通过。

| 执行 | 实际结果 | 证据 |
| --- | --- | --- |
| 首次命令 | 2个导入错误，非业务复现 | [日志](import-error.txt) |
| 原产品+新TCP用例 | 2/2按预期失败，BrokenPipe | [日志](red.txt) |
| 两套HTTP回归 | 23/23，20.121秒 | [命令](http-command.txt)、[日志](http-green.txt) |
| 完整MCP+Search HTTP | 139/139，97.971秒 | [命令](full-command.txt)、[日志](full.txt) |
| Agent/Search各重复10次 | 20/20，17.988秒 | [命令](repeat-command.txt)、[日志](repeat.txt) |

完整139项为MCP目录127项与Search HTTP12项，不是整个仓库的所有测试。
边界用例同时覆盖空闲拒绝连接和持续滴流：下一连接在1秒测试容差内收到503，
后端/编码器调用为零。该容差检查有界性，不作为响应时间性能成绩。
完整日志保留一条asyncio慢任务诊断及SDK负向用例的tool-not-listed警告；未隐藏警告或放宽断言。
独立只读审查未发现可操作问题，指出同步排空的延迟及超限后仍可能断连的限制；未替代实测。

[前置](preflight.txt)/[后置](postflight.txt)25个Python源码与测试文件逐项相同，且与运行副本一致；
[检查器](fingerprints.py)仅适用于本提交和所记录运行目录，不应在未来代码改变后改写历史指纹。
[资源终态](resources.txt)：Python3.10.12/Linux5.15；CPU配额1核、MemoryHigh1200MiB、
MemoryMax1600MiB、Swap0、TasksMax256、磁盘读写各5MB/s/200IOPS。
TasksCurrent=0，MySQL仍active；memory.events high82471与运行前相同，max/oom/oom_kill均0。
最终MemAvailable1614440KiB；这是抽样，不是连续内存峰值或性能测量。

复验需在同等受控环境准备运行副本、依赖和权限，再执行所附命令；不要在旧单元仍活跃时重启或复用名称。
红色阶段使用基线search_http.py与本提交新增测试，绿色阶段使用本提交产品代码；
命令导入必须包含test/mcp及test/python（定向执行），完整套件使用独立TestLoader.discover。

## 未覆盖与下一步

本轮只修复满载拒绝分支的已复现关闭竞态，不声称所有错误响应/任意网络时序都保证送达。
未复跑真实模型、C++/数据库端到端或公网反向代理链路，未改变30项任务台账、用量或人工引用判定。
下一增量补足H05状态查询被拒的诊断证据，再决定报告表述修正与H07复验；
人工语义/业务评审、剩余真实任务和完整业务演示仍未完成，M5/P0保持进行中。
