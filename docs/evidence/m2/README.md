# M2 阶段验收索引

状态：**M2 功能阶段已验收关闭（2026-10-18），本次停止，不启动 M3**。
最终被测代码 `e72e0abadda256472888560671429c892da3d041`；见[关闭审计](stage-audit.md)。
[中文/扫描PDF补验](pdf-gates/README.md)与[物理页说明](physical-pages/README.md)
已关闭最后两类样例及UI说明缺口。最近真实混合组合被测提交为
`4adaec5cb16816ee408e292868e2c6699743c3e4`；[组合复验](transient-browser/README.md)
首次 0/1、计时诊断轮 1/1，不抹去失败。正式集成源码见
[分词生命周期证据](transient-integration/README.md)。本索引映射
[需求第 6/10/14 节](../../../团队文档管理与智能整理平台_需求文档.md)和
[已批准方案 A](../../superpowers/specs/2026-10-17-m2-document-processing-design.md)。
“有分层证据”不等于“本轮重跑”，历史测试数量不累计成一个新分母。

| M2 门槛 | 已有证据及边界 | 结论/边界 |
| --- | --- | --- |
| PDF/TXT/Markdown 真实解析、明确失败 | [当前补验](pdf-gates/README.md)完整Python104/104、真实worker7/7，包含中文PDF和纯图片扫描PDF明确失败、原件可管理下载 | 通过；不扩大到OCR |
| 物理页/文本行、固定版本出处 | [三格式来源](source-locations/README.md)验证SHA/版本及历史引用；[物理页说明](physical-pages/README.md)和[中文双页页面](pdf-gates/README.md)验证固定链接、物理页/行及原始字节 | 通过；本轮未重跑原生PDF渲染，M1既有证据不改写 |
| 持久任务、人工取消/重试、独立状态 | [处理 UI](processing-ui/README.md)真实失败→重试→取消→刷新→完成及 reader 403；[当前 worker](source-locations/README.md)重新验证 C++ 重启中断/人工重试及锁恢复 | 本地解析 worker 证据已归档；常驻编码中断联合场景见下方，不声称任意内部步骤自动恢复 |
| 真实混合检索及明确降级 | [优化后组合](transient-browser/README.md)计时诊断轮 1/1，真实向量跨语言命中与明确关键词降级均通过；同轮首次 0/1 为浏览器控制超时，两次合计 1/2 | 语义闭环已有实际通过；保留启动/IO波动风险，不声明稳定性或性能 SLA |
| 新版/旧 attempt 防陈旧发布 | [向量发布](vector-publication/README.md)、[混合后端](hybrid-search/README.md)、[关键词版本](keyword-version-browser/README.md)覆盖；[新组合](transient-browser/README.md)验证新版未解析时旧正文排除、关键词只返回新版、旧出处保持旧版 | 通过；不放宽结果身份检查 |
| 越权、删除、外发撤销及批准不继承 | [处理 UI](processing-ui/README.md)与其完整 C++ 输出、[向量 worker](vector-worker/README.md)、[混合后端](hybrid-search/README.md)分层覆盖；C++ 仍为唯一权威 | 不要求把全部分支放到一个浏览器场景；新常驻入口不得绕过编码前后 C++ 检查 |
| 服务故障与恢复可解释 | [常驻业务恢复](resident-recovery/README.md)当前真实联合场景 1/1：第二批请求中停止服务，第一批暂存不参与向量检索；failed 跨 C++ 重启保持，编码服务恢复不触发自动重试，显式 retry 后 attempt2 完成两批9片段并恢复 hybrid | 本项已补齐可控中断场景；不声称覆盖内核 D 状态、任意宕机窗口或长期稳定性 |

## 当前证据一致性

最近影响 C++ 业务及其处理测试的提交为 `729d2b5`；相关 C++/数据库/脚本差异为空。
物理页说明轮核对 `processing-ui/source-sha256.txt` 的11项中10项一致，app.html因
页码标签改变；search.js亦有说明改动，不在那份11项清单中。历史 **141/141** 仍
为未变C++的分层证据；上一轮前端 **38/38**、契约 **8/8**对应当前未变化前端。
最终补验重新执行Python **104/104**、worker **7/7**、真实页面 **1/1**。
详细指纹与范围见[阶段审计](stage-audit.md)。Python 常驻模式的历史 **95/95** 与真实服务
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
[物理页说明](physical-pages/README.md)通过真实页面三种宽度检查；最终
[中文和扫描PDF补验](pdf-gates/README.md)已填补阶段审计剩余缺口。
据此完成M2功能门槛核验，不宣称消除资源波动。

最终被测提交、命令、原始日志、源哈希与清理终态已归档；阶段交接只更新文档。
100 份资料规模、并发吞吐、长期稳定性及 Agent 效果分母不在本轮伪造或扩大；
需求中的更大评测在 M5 实测。没有生产常驻部署、远程 API 调用或 M3–M5 完成声明。
