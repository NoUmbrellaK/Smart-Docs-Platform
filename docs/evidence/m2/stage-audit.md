# M2 阶段证据审计（2026-10-18）

审计基线 `329df1df2c43b2659505f85bd50a7a69a17801a7`，另含本次物理页码 UI
说明及对应测试。结论：**暂不关闭 M2，不进入 M3**。剩余验证项是中文 PDF
提取/页码定位及纯图片扫描 PDF 的真实 worker 失败场景；不是服务器内存不足
导致的阻塞，也不需为此调用模型。

## 需求与证据

| 范围 | 已核实的证据与限制 |
| --- | --- |
| R-01 三格式、超限与失败 | [真实 worker](source-locations/README.md)7/7；PDF 子进程的损坏、空白页、加密、超限、警告拒绝及资源终止由 `test/python/test_pdf_parser.py` 覆盖，包含于历史完整 Python 102/102。PDF 双页夹具为 PAGE ONE/TWO，**未证明中文 PDF 提取**；空白页也不能替代纯图片扫描件的实际 worker 证据。 |
| R-01 固定版本、物理页/行 | 三格式来源逐字段核对版本、SHA、稳定 chunk、行号、PDF 1/2 页；旧引用不跳新版、跨版本 chunk 拒绝；本次新增物理页从 1 起及与印刷页码区别的 UI 文案。 |
| R-02 元数据、权限与范围 | 完整 C++ 141/141 中 `processing_search_metadata_*`、`processing_search_enforces_roles_*`、`processing_search_limits_*` 覆盖未解析发现、分页、项目/目录/类型及角色；`processing_hybrid_*` 覆盖获批范围、融合、去重、预算、降级与撤销竞态。协议单位向量夹具不作为真实语义证据。 |
| R-02 真实语义与降级 | [优化后真实浏览器组合](transient-browser/README.md)首次0/1、计时诊断1/1；获批英文资料被中文问题向量命中、来源固定，编码不可用明确关键词降级。保留1/2和资源波动风险，不宣称稳定性 SLA。 |
| R-03 更新、删除、移动、还原 | C++ `processing_search_filters_*`、`processing_search_revalidates_candidates_*`、`processing_publication_*` 检查当前版本、目录、删除/还原和旧 attempt；真实组合补充新版未解析时旧正文排除、新版批准不继承与旧来源仍可读。 |
| 第10节 任务、人工重试 | [处理页面](processing-ui/README.md)真实失败→重试→取消→刷新→完成，reader写403；worker7/7含C++重启中断；[常驻业务恢复](resident-recovery/README.md)1/1验证请求中断、不发布部分向量、持久失败、服务恢复不自动重试、人工retry完成。队列/单执行/attempt边界另有C++测试，不声称任意内部步骤恢复。 |
| 第14节 M2门槛 | 当前版本、出处定位和人工重试均有分层及真实联合证据；仍须补第16节明确要求的中文提取样例，以及 R-01 的实际扫描件失败样例。 |

## 本次一致性检查

- `git diff --exit-code 729d2b5 -- code db Makefile test/integration test/unit scripts`
  无差异。因此 [processing-ui/verified-check.txt](processing-ui/verified-check.txt)
  的 `RESULT 141 passed, 0 failed, 0 skipped` 是未变化 C++ 的历史回归证据，
  **不是本轮重跑**。
- [processing-ui 指纹](processing-ui/source-sha256.txt)11项中10项一致；唯一变化
  为 `resources/app.html` 的物理页标签。本次还修改 `resources/js/search.js`，它
  不在这份11项清单中，不能笼统宣称所有前端未变。本轮前端行为38/38及契约8/8。
- [transient-integration 指纹](transient-integration/source-sha256.txt)9项全部一致；
  `git diff --exit-code 4adaec5 -- python test/python` 亦无差异。
  其原始日志实际记录102项通过，不把旧的59/64/95项累计成新分母。
- [resident-recovery 指纹](resident-recovery/source-sha256.txt)13项全部一致，包含
  实际执行的server/admin二进制；本轮没有重新编译，也不使用工作树中的旧bin。
- 本次校验原始输出见 [hash-audit.txt](physical-pages/hash-audit.txt)。app.html 的
  `FAILED` 是预期的源码差异报告，不是运行测试失败；差异须由本轮覆盖替代。

## 下一增量及不纳入 M2 的事项

下一增量：准备来源清楚的两页可提取中文 PDF 小夹具，以人工预期正文、物理
页码、行号及 Unicode 偏移验证真实受限解析和固定版本出处；另加纯图片 PDF，
经真实 worker 验证 failed/no_extractable_text、零片段、元数据可发现及原件可
下载。前者为明确中文覆盖缺口，后者为已有无文本路径的真实场景证据缺口，
不是已发现的生产缺陷。补齐后复核阶段结论；不扩大解析上限、不加载模型，
不重做无变化的大型混合浏览器组合。独立只读复核确认这两项，其余未发现
明确 M2 门槛缺口。

R-02 的 Agent 工具元数据模式及 R-04 的回答/报告语义、MCP契约、真实确认与归档
属于 M3/M4；100份资料、固定任务效果与引用支持度分母、规模性能和完整演示在
M5实测。它们不能被宣布完成，也不应伪装成 M2 本轮新阻塞。已有本机 worker、
向量服务和同源网关部署模板，不等于生产常驻/TLS已部署。限额和失败历史继续保留。
