# Q01：查找当前接口说明的首次真实执行

2026-11-05，代码基线 `0f1369146d0a0352e3f31fbed2f10c6e2ec41a85`。
使用冻结Q01，不改产品、提示词或判据；按用户“批准多次测试，不用审批”的持续同范围授权执行一次。
只向固定DeepSeek端点发送已批准的人工接口说明当前版本、问题、系统提示和工具定义/结果，
不发送业务资料、真实用户会话或期望答案；上限6次请求、每次512输出token，失败不自动重跑，不确认归档。

## 实际输入与结果

冻结输入：[tasks.json](../../../../test/fixtures/agent-p0/tasks.json)的Q01；
[corpus.json](../../../../test/fixtures/agent-p0/corpus.json)同时装载接口说明v1和v2，但任务允许范围只有当前api@2。
旧版是/api/v1/materials、默认16；期望当前/api/v2/materials、默认20、最大50，并引用当前版本。

[结果](result.json)和[审计摘要](audit-summary.json)一致：completed，采集完整。
实际2次供应商请求、2次search_documents，无read_document_version、报告保存或归档工具。
[10条脱敏轨迹](trace.jsonl)显示第一次响应同时提出hybrid和metadata两个查询；
第二次响应给出答案。因此query_changed=true只是同一批次两个查询不同，**不能证明收到空结果后动态改查**，
不作为新增T-10证据。搜索返回item_count=1；chunk_count=0未统计嵌套片段，不能据此断言没有原文。

AI辅助检查确认答案包含GET /api/v2/materials、默认20、最多50及next_cursor结束条件，
没有沿用旧版路径。唯一引用绑定当前api@2的file/version/chunk，行1–5；
采集原文、源SHA和冻结fixture映射一致，旧版和当前版保持同file、不同version。
答案明确keyword_only、vector_state pending，并声明没有全文逐段核对。
运行时没有编码服务，因此不是混合检索效果验收；完整原始答案及固定原文留在私有快照。

这些是可定位性及AI辅助内容检查，不是人工语义判定。
任务只选定1份文件，评审时仍需核对答案“当前项目”的范围表述是否足够明确，
不能将1份命中理解为遍历全项目。business_verdict和citation_accuracy均为null。

## 用量与资源

| 请求 | 输入token | 输出token | 总计 |
| --- | ---: | ---: | ---: |
| 1 | 1398 | 145 | 1543 |
| 2 | 2449 | 240 | 2689 |
| 合计 | 3847 | 385 | 4232 |

模型deepseek-flash；实际max_tokens均512；缓存命中1024、未命中2823。金额未查询，不记为零费用。
H01历史仍为36次/220188 token；两个固定案例累计38次/224420 token，不含此前M3烟测。
固定30项目前为3项准入拒绝观测、2项执行完成、25项模型未执行；完成执行不等于业务通过。
历史引用由17增至18（H01各轮17+Q01本轮1），全部保留；人工已评0，不报告准确率。

[运行指纹](preflight.txt)核对37项源码/二进制与受限副本一致；
[启动命令与实时配置](launch.txt)沿用CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
TasksMax256、磁盘读写各5MB/s/200IOPS，nobody/nogroup、Nice10、UMask0077、NoNewPrivileges，
600秒运行上限、KillMode=control-group。没有提高配额或并行启动其他测试。

[终态日志](journal-and-status.txt)：20:42:49–20:44:38 Asia/Shanghai，正常退出后瞬态单元回收；
TasksCurrent=0、原mysql active，memory.high仍82471，max/oom/oom_kill均0。
观察可用内存约1928–2084MiB，最终2061MiB，是采样值而非峰值。
10条迁移PASS不计入业务任务分母，没有重跑全套产品回归。
包装器清理了本轮临时SQL库与测试文件，不能恢复，可按冻结夹具再生成；没有删除业务资料。
初始化空密码警告来自禁用网络的隔离MySQL，不是生产数据库变更。

## 证据与下一步

原件及空白评审表：`/tmp/smartdocs-m5-q01-live.9xrdwh/Q01`，目录0700、文件0600、nobody所有。
公开结果包含四份原件SHA-256；原始快照、答案和来源正文不提交。
这是临时保存，不是持久备份；需按既有[离线评审规则](../../m3/benchmark-review/README.md)受控保留并由人工判定。
[交叉核验](verification.txt)覆盖公开/私有一致性、实际调用与用量、引用当前版本、原件权限和30项台账分母。

本增量结束后不重跑Q01。下一步按冻结输入继续Q02等剩余任务，保留失败和全部引用；
人工评审与至少50条引用样本、完整演示等M5交付仍待完成，不以本次执行完成关闭P0。
