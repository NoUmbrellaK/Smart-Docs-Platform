# M2 阶段验收索引

状态：**尚未关闭 M2，不进入 M3**。截至本轮被测提交
`5e188d66f3ae765ab861a2268cecc34cde6a26a3`，本索引映射
[需求第 6/10/14 节](../../../团队文档管理与智能整理平台_需求文档.md)和
[已批准方案 A](../../superpowers/specs/2026-10-17-m2-document-processing-design.md)。
“有分层证据”不等于“本轮重跑”，历史测试数量不累计成一个新分母。

| M2 门槛 | 已有证据及边界 | 关闭前待办 |
| --- | --- | --- |
| PDF/TXT/Markdown 真实解析、明确失败 | [worker 旧验收](../../superpowers/plans/2026-10-17-m2-local-worker.md#2026-10-17-验证记录)报告 7/7 真实 HTTP，但原始日志在临时目录；[当前 Python 回归](resident-service/python-regression.txt)覆盖解析器边界 | 在当前代码重跑真实 worker 并将原始输出永久归档 |
| 物理页/文本行、固定版本出处 | [处理页](processing-ui/README.md)、[混合后端](hybrid-search/README.md)有分层记录；真实三格式 worker 测试目前明确读取 PDF 第 1 页，未逐页断言两页出处 | 补强两页 PDF 与 TXT/MD 行范围和 version_id 的真实来源证据；验证 PDF 页定位链接 |
| 持久任务、人工取消/重试、独立状态 | [处理 UI](processing-ui/README.md)真实失败→重试→取消→刷新→完成及 reader 403；[worker](../../superpowers/plans/2026-10-17-m2-local-worker.md#2026-10-17-验证记录)记录 C++ 重启中断/人工重试 | 将当前 worker 复验归档；不声称任意内部步骤自动恢复 |
| 真实混合检索及明确降级 | [混合后端](hybrid-search/README.md)和[网关](hybrid-gateway/README.md)有真实模型/HTTP 证据；[常驻监督](resident-service/README.md)保留原客户端和完整旧向量指纹；[本轮组合](resident-browser/README.md) **0/1**，Chrome 创建会话失败、尚未编码；两个启动隔离诊断通过但有回收/IO 压力 | 当前常驻模式需通过原浏览器组合场景，不能拼接旧失败轮次；先测模型保留内存，不原样重跑 |
| 新版/旧 attempt 防陈旧发布 | [向量发布](vector-publication/README.md)、[混合后端](hybrid-search/README.md)、[关键词版本](keyword-version-browser/README.md)覆盖；完整 C++ 141/141 包含当前/删除/恢复/候选返回前复核 | 纳入本轮原浏览器组合的新旧版本断言，不放宽结果身份检查 |
| 越权、删除、外发撤销及批准不继承 | [处理 UI](processing-ui/README.md)与其完整 C++ 输出、[向量 worker](vector-worker/README.md)、[混合后端](hybrid-search/README.md)分层覆盖；C++ 仍为唯一权威 | 不要求把全部分支放到一个浏览器场景；新常驻入口不得绕过编码前后 C++ 检查 |
| 服务故障与恢复可解释 | [常驻监督](resident-service/README.md)真实进程验证超时、崩溃、回收和不重放；旧 worker 有持久失败/重试记录 | 补新常驻模式的“请求期间停止服务→无部分发布且持久失败→重启→仅显式 retry 后完成”真实联合场景 |

## 当前证据一致性

最近影响 C++ 业务及其处理测试的提交为 `729d2b5`。本轮核对
`processing-ui/source-sha256.txt` 所列 11 个文件全部一致，包括处理服务、路由、
前端、处理测试和浏览器用例；因此其历史 **141/141** 可以作为对应未变化代码的
分层证据，但不冒称本轮完整重跑。Python 常驻模式的最终 **95/95** 与真实服务
**1/1、6 项检查**绑定其独立源码指纹，不代替数据库、业务权限或浏览器验收。

后续[堆内存诊断](resident-heap/README.md)已否定简单 GC/trim：主要常驻占用与
存活分词器/模型相关；下一步先验证分词器短生命周期，不原样重跑完整组合。
原浏览器失败及其余验收门槛仍保留，不能用内存诊断替代。

M2 关闭前按表补齐并更新最终被测提交、命令、原始日志、源哈希与清理终态。
100 份资料规模、并发吞吐、长期稳定性及 Agent 效果分母不在本轮伪造或扩大；
需求中的更大评测在 M5 实测。没有生产常驻部署、远程 API 调用或 M3–M5 完成声明。
