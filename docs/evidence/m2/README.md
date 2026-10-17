# M2 阶段验收索引

状态：**尚未关闭 M2，不进入 M3**。最近组合被测提交为
`4adaec5cb16816ee408e292868e2c6699743c3e4`；[组合复验](transient-browser/README.md)
首次 0/1、计时诊断轮 1/1，不抹去失败。正式集成源码见
[分词生命周期证据](transient-integration/README.md)。本索引映射
[需求第 6/10/14 节](../../../团队文档管理与智能整理平台_需求文档.md)和
[已批准方案 A](../../superpowers/specs/2026-10-17-m2-document-processing-design.md)。
“有分层证据”不等于“本轮重跑”，历史测试数量不累计成一个新分母。

| M2 门槛 | 已有证据及边界 | 关闭前待办 |
| --- | --- | --- |
| PDF/TXT/Markdown 真实解析、明确失败 | [当前三格式 worker](source-locations/README.md)7/7，完整日志永久归档；覆盖明确解析失败及原件仍可管理/下载 | 本项当前代码复验完成；不扩大到 OCR 或未支持格式 |
| 物理页/文本行、固定版本出处 | [三格式来源](source-locations/README.md)逐字段验证 PDF 两页、TXT/MD 行范围、SHA/版本及历史引用；真实页面最终 1/1（含初版失败为 1/2），固定版本链接、页锚点及受保护原始字节通过 | R-01 的 UI“从 1 开始的物理页码，与印刷页码可能不同”说明仍待补；本轮未重跑原生 PDF 渲染 |
| 持久任务、人工取消/重试、独立状态 | [处理 UI](processing-ui/README.md)真实失败→重试→取消→刷新→完成及 reader 403；[当前 worker](source-locations/README.md)重新验证 C++ 重启中断/人工重试及锁恢复 | 本地解析 worker 证据已归档；常驻编码中断联合场景见下方，不声称任意内部步骤自动恢复 |
| 真实混合检索及明确降级 | [优化后组合](transient-browser/README.md)计时诊断轮 1/1，真实向量跨语言命中与明确关键词降级均通过；同轮首次 0/1 为浏览器控制超时，两次合计 1/2 | 语义闭环已有实际通过；保留启动/IO波动风险，不声明稳定性或性能 SLA |
| 新版/旧 attempt 防陈旧发布 | [向量发布](vector-publication/README.md)、[混合后端](hybrid-search/README.md)、[关键词版本](keyword-version-browser/README.md)覆盖；[新组合](transient-browser/README.md)验证新版未解析时旧正文排除、关键词只返回新版、旧出处保持旧版 | 已有分层及本轮页面证据；其余门槛仍需补齐，不放宽结果身份检查 |
| 越权、删除、外发撤销及批准不继承 | [处理 UI](processing-ui/README.md)与其完整 C++ 输出、[向量 worker](vector-worker/README.md)、[混合后端](hybrid-search/README.md)分层覆盖；C++ 仍为唯一权威 | 不要求把全部分支放到一个浏览器场景；新常驻入口不得绕过编码前后 C++ 检查 |
| 服务故障与恢复可解释 | [常驻业务恢复](resident-recovery/README.md)当前真实联合场景 1/1：第二批请求中停止服务，第一批暂存不参与向量检索；failed 跨 C++ 重启保持，编码服务恢复不触发自动重试，显式 retry 后 attempt2 完成两批9片段并恢复 hybrid | 本项已补齐可控中断场景；不声称覆盖内核 D 状态、任意宕机窗口或长期稳定性 |

## 当前证据一致性

最近影响 C++ 业务及其处理测试的提交为 `729d2b5`。本轮核对
`processing-ui/source-sha256.txt` 所列 11 个文件全部一致，包括处理服务、路由、
前端、处理测试和浏览器用例；因此其历史 **141/141** 可以作为对应未变化代码的
分层证据，但不冒称本轮完整重跑。Python 常驻模式的最终 **95/95** 与真实服务
**1/1、6 项检查**绑定其独立源码指纹，不代替数据库、业务权限或浏览器验收。

后续[堆内存诊断](resident-heap/README.md)已否定简单 GC/trim：主要常驻占用与
存活分词器/模型相关；[分词短生命周期原型](transient-tokenizer/README.md)已证明
五次旧指纹与更低的请求间内存占用。后续[正式集成](transient-integration/README.md)
完整 Python **102/102**、真实核心 9 项、私有服务 7 项检查通过；新增分词孙进程
挂起的总截止期/回收验证，请求间模型 RSS 约 373–376 MiB。
[后续组合](transient-browser/README.md)原断言在计时诊断轮 1/1 通过，第一次
0/1 同样保留；不是仅以单模型结果代替。[三格式出处补验](source-locations/README.md)
已补齐当前 worker 7/7 和页面最终 1/1；页面初版因 ngram 精确匹配假设错误 0/1，
修正目标文件/版本范围后通过，失败记录保留。[常驻业务恢复](resident-recovery/README.md)
已补真实请求中断→失败持久化→重启不自动重试→人工 retry 后完成，1/1。
下一步补物理页码说明并做 M2 全阶段证据审计；不能据此直接关闭 M2 或宣称消除资源波动。

M2 关闭前按表补齐并更新最终被测提交、命令、原始日志、源哈希与清理终态。
100 份资料规模、并发吞吐、长期稳定性及 Agent 效果分母不在本轮伪造或扩大；
需求中的更大评测在 M5 实测。没有生产常驻部署、远程 API 调用或 M3–M5 完成声明。
