# P0 验收证据索引

核对日期：2026-10-20。实现基线：`c5f106d`。
依据[需求第13节](../../../团队文档管理与智能整理平台_需求文档.md)，逐项关联测试入口、实际记录与未证明的范围。
**M1至M4功能阶段已关闭，P0最终验收尚未完成。** 本索引不是一次新的全套测试结果。

“工程证据”指对应实现已有实际功能/故障测试；不等于当前发布版本已重新运行全部测试，
也不等于真实模型达到了业务判据。下表的运行记录都是已归档历史记录，
本次只核对内容、测试断言、版本差异和文件一致性，不新增产品通过次数。
不同轮次、断言数量、迁移检查和协议夹具不能相加成为业务任务分母。

## 文件与检索

| 编号 | 必须证明的行为 | 测试入口和实际证据 | 已有范围及剩余限制 |
| --- | --- | --- | --- |
| T-01 | 项目/角色限制覆盖列表、下载、Range、预览、检索、工具和报告，替换ID不能越权 | [M1 HTTP](../../../test/e2e/m1_http_test.py)的T-01及[结果](../m1/verified/results.json)；[processing_jobs_test.cpp](../../../test/integration/processing_jobs_test.cpp)的`processing_search_enforces_roles_input_limits_and_same_origin`；[五工具权限矩阵](../m3/provider-reasons/matrix.txt)；[报告HTTP与浏览器](../m4/functional-gate/journal-and-status.txt) | 分层工程证据。MCP矩阵的权限列是同项目其他任务所有者，不冒充跨项目覆盖；跨项目及角色由文件/检索用例补充。报告是个人草稿，同项目管理员也不能代读。 |
| T-02 | 同分片同内容幂等，不同内容冲突 | [upload_part_test.cpp](../../../test/integration/upload_part_test.cpp)、M1 HTTP T-02；[M1结果](../m1/verified/results.json) | 工程证据；无静默覆盖。M1记录绑定`ed14dcf`，不是当前HEAD的重新执行。 |
| T-03 | 刷新/重启后只补真实缺失分片，最终哈希一致 | [m1_interrupt_test.py](../../../test/e2e/m1_interrupt_test.py)、[m1_browser_test.py](../../../test/e2e/m1_browser_test.py)的`refresh_resume_reselect`；[20轮与浏览器结果](../m1/verified/results.json) | 工程证据；六类故障位置、确认分片、错误重选文件不发PUT、最终SHA均有断言。20轮不是20次整机断电。 |
| T-04 | 重复complete/丢响应返回同一文件、版本、处理任务，半成品不发布 | [upload_complete_test.cpp](../../../test/integration/upload_complete_test.cpp)、M1 HTTP T-04及20轮中断；[M1结果](../m1/verified/results.json) | 工程证据；中断后的发布和单例对象检查不能替代解析完成，上传成功不自动宣称已索引。 |
| T-05 | 单段Range字节/长度/状态正确，无效范围被拒绝，PDF真实翻页 | [file_download_test.cpp](../../../test/integration/file_download_test.cpp)、M1 HTTP T-05、浏览器`protected_pdf_page_navigation`；[浏览器原始结果](../m1/verified/browser.json) | 工程证据；真实PDF页面截图与文档视口哈希、page=1/2均有记录，不把Range接口通过当作预览已实现。 |
| T-06 | 默认检索仅当前有效版本，固定历史来源不跳新版 | [processing_jobs_test.cpp](../../../test/integration/processing_jobs_test.cpp)的`processing_document_history_never_redirects_and_rechecks_access`及`processing_search_filters_current_deleted_unavailable_and_restored_versions`；[M2来源补验](../m2/pdf-gates/python-worker-journal.txt)、[报告固定来源复验](../m4/functional-gate/journal-and-status.txt) | 工程证据；历史引用可继续绑定旧版，不等于允许旧版重新进入默认正文检索。文件软删除/撤权后仍重鉴权。 |
| T-07 | 旧解析/向量任务晚完成也不能发布为当前索引 | [processing_jobs_test.cpp](../../../test/integration/processing_jobs_test.cpp)的`processing_publication_rejects_stale_cancelled_and_restarted_attempts`及`processing_vectors_cannot_read_or_publish_superseded_deleted_or_unavailable_source`；[分组C++日志](../m3/task-scope/cpp-group-1-journal.txt)、[worker补验](../m2/pdf-gates/python-worker-journal.txt) | 工程证据；同时检查结果/片段未发布和attempt隔离。**M1脚本中同名T-07只测旧上传冲突，不作为本条旧索引发布证据。** |
| T-08 | 更新、删除、还原、权限和外发变化影响所有后续读取；未显式引用的已准入来源也受限 | `processing_search_filters_current_deleted_unavailable_and_restored_versions`、`processing_search_revalidates_candidates_after_concurrent_state_changes`；[C++分组记录](../m3/task-scope/cpp-group-1-journal.txt)；报告`test_report_uncited_dependency_revocation_blocks_all_content_and_replay`和真实页面[复验](../m4/functional-gate/journal-and-status.txt) | 工程证据；来源台账含已准入但未引用资料，页面失败清屏，报告正文/导出重新检查。已下载的用户副本不能通过改标签远程撤回。 |
| T-09 | 未解析/扫描/损坏/超限/向量失败可被发现，并如实报告失败或关键词降级 | [m2_worker_test.py](../../../test/e2e/m2_worker_test.py)的`test_unsupported_and_broken_files_remain_managed_but_not_indexed`；[中文/纯图片PDF日志](../m2/pdf-gates/python-worker-journal.txt)；[真实混合与降级](../m2/transient-browser/README.md)；`processing_search_metadata_discovers_unparsed_and_literal_names_with_pagination` | 工程证据；扫描件是有图像操作的人工PDF，不是空白页，未实现OCR。真实混合组合历史有1次失败和1次通过。**M1的T-09仅验证pending，不足以证明本条解析/向量失败行为。** |

## Agent 与写操作

| 编号 | 必须证明的行为 | 测试入口和实际证据 | 已有范围及剩余限制 |
| --- | --- | --- | --- |
| T-10 | 首次搜索不足后由模型调整下一步 | [m3_deepseek_test.py](../../../test/e2e/m3_deepseek_test.py)、[第三次真实运行](../m3/live-public-20261020-3/journal-and-status.txt)及[逐事件轨迹](../m3/live-public-20261020-3/trace.jsonl) | 真实供应商直接证据：搜索0项→改查找到→读取→带固定引用保存，4次请求。本次1/1，三次累计1/3；不是30项成功率，不推导全部模式质量。 |
| T-11 | 缺失、冲突、分页未完成时区分已找到/待补充/待确认并说明范围 | [handover_reports.cpp](../../../code/agent/handover_reports.cpp)、[m3_handover_report_test.py](../../../test/e2e/m3_handover_report_test.py)及[报告HTTP/页面复验](../m4/functional-gate/journal-and-status.txt)；[冻结H01–H10](../../../test/fixtures/agent-p0/tasks.json) | **部分证据。** 清单结构、状态、空证据不能标found、范围/限制展示已有工程测试；真实模型对缺失/冲突/未搜索完资料的业务判断仍须固定任务与人工评审，尚无完成率。 |
| T-12 | 文档指令不能改变权限、触发外发、产生确认或越界工具 | [m3_injection_boundary_test.py](../../../test/e2e/m3_injection_boundary_test.py)与[4场景日志](../m3/injection-boundary/run.txt)；[归档未确认拒绝](../m4/boundary-recheck/journal-and-status.txt) | **局部安全证据。** 恶意正文被真实读取后，脚本模型输出删除/系统命令/伪确认/冒用身份，执行器在派发前拒绝；文件、批准、成员和业务写对象不变。这4项处于只读模式，不能称所有模式或真实模型抗注入评测；固定业务集中的注入项仍未执行。 |
| T-13 | 未确认、拒绝和修改修订都不能沿用旧授权移动 | [m3_archive_execution_test.py](../../../test/e2e/m3_archive_execution_test.py)的`test_execution_waiting_releases_slot_and_never_accepts_model_approval`、`test_execution_reject_old_digest_revision_and_unresolved_targets`；[后端22项](../m4/boundary-recheck/journal-and-status.txt)、[人工修改真实页面](../m4/manual-revision/README.md) | 工程证据；网页修改同方案产生新修订，保存不移动，旧确认失效，必须新确认。人工修改不冒充模型重新分类。 |
| T-14 | 确认后版本、源位置、目标目录、权限变化使整批冲突 | `test_execution_rechecks_location_and_target_policy_after_confirmation`、`test_execution_whole_scope_revocation_and_new_version_block_moves`、`test_execution_new_destination_conflict_after_confirmation_blocks_whole_batch`；[后端复验](../m4/boundary-recheck/journal-and-status.txt) | 工程证据；身份/角色/取消另由`test_execution_checks_owner_role_origin_and_cancel_before_commit`覆盖。不是只验证计划创建时的检查。 |
| T-15 | 批量归档中途故障全成或全败，不留部分移动或虚假成功 | `test_execution_database_failure_after_first_move_rolls_back_all_business_state`；[后端复验](../m4/boundary-recheck/journal-and-status.txt) | 事务故障直接证据：SQL触发器仅在第一份已更新后对第二份报错；两份位置、操作结果、审计均回滚，GET回执404，移除故障后整批成功。不是整机断电测试。 |
| T-16 | 写入提交但回包丢失时查询同一操作，不重复移动或保存 | 归档`test_execution_lost_body_restart_and_later_move_do_not_repeat_operation`；报告`test_report_lost_body_restart_and_expired_run_replays_original`；[归档/报告MCP](../m4/boundary-recheck/journal-and-status.txt)、[报告HTTP](../m4/functional-gate/journal-and-status.txt)、[写超时SQL](../m3/tool-timeout/sql.txt) | 工程证据；已提交回执与当前文件位置是不同事实。未知写结果不自动重写；未知未提交分支另有本地接收器测试，不扩大为真实断网覆盖。 |
| T-17 | 不存在/无权引用被拒绝；合法但不支持结论的引用判错 | 报告`test_report_unadmitted_or_forged_citation_cannot_save`及[HTTP复验](../m4/functional-gate/journal-and-status.txt)；[benchmark_review.py](../../../test/e2e/benchmark_review.py)与[评审/采集测试](result-capture/verified.txt) | **部分证据。** 固定ID、已准入来源、当前权限的工程门禁已测；离线工具能记录wrong_location/unsupported_claim，但尚未对真实固定任务的全部引用判定。格式通过不能证明语义正确。 |
| T-18 | 内部资料/未批准新版不得进入远程处理，新版不继承批准 | [远程读取边界及原始分组记录](../m3/remote-read/README.md)、[m3_agent_http_test.py](../../../test/e2e/m3_agent_http_test.py)的`test_http_dynamic_search_private_owner_and_revocation`、[供应商接收器与HTTP记录](../m3/provider-reasons/regression.txt)、[注入接收器检查](../m3/injection-boundary/run.txt) | 分层工程证据：C++检查批准/版本/范围，撤销后供应商请求数不再增加，模拟接收器断言不含内部名称/正文。不是让真实云端接收受限资料以验证拒绝，也不承诺自动识别任意敏感用户输入。 |
| T-19 | 超时、限流、预算耗尽、取消如实终止，不继续新写入 | [test_agent_runner.py](../../../test/mcp/test_agent_runner.py)的预算/超时用例与[109项协议回归](../m3/json-output/regression.txt)；[五类失败原因HTTP](../m3/provider-reasons/regression.txt)、[工具超时SQL](../m3/tool-timeout/sql.txt)、HTTP在途取消用例 | 工程证据；供应商失败用MockTransport，工具超时是响应后注入，不声称真实外网断链/15秒等待。持久失败码、取消终态、一次调用、未知写入仅查询等分别有断言。 |

## 恢复

| 编号 | 必须证明的行为 | 测试入口和实际证据 | 已有范围及剩余限制 |
| --- | --- | --- | --- |
| T-20 | 恢复后文件、版本、成员权限、固定引用匹配，索引能重建 | [m3_backup_restore_test.py](../../../test/e2e/m3_backup_restore_test.py)的`--vectors`模式；[完整向量恢复原始记录](../m3/vector-restore/verified.txt)、[默认模式回归](../m3/vector-restore/default-regression.txt)及[操作说明](../m3/vector-restore/README.md) | 本机隔离恢复直接证据：全库行摘要与成对storage、9存储文件/4版本/角色/私人报告/固定来源/续传；真实本地向量重建与关键词降级。新库新目录，原库/备份不变。不是生产恢复、异机兼容、冷模型缓存或RPO/RTO证明。 |

## 版本绑定与发布复验

当前源码不能仅凭旧绿色记录自动继承完整验收。实际只读差异核对：

- `git diff --name-only ed14dcf..c5f106d -- code/upload code/file code/auth code/http code/server`
  存在5个变更文件：file_repository.cpp、file_service.cpp、httpresponse.cpp/h、
  upload_repository.cpp。M1的20轮、浏览器和流式记录仍是有效历史证据，
  **不能称其二进制或完整文件链路与当前版本未变**。
- `git diff --name-only e72e0ab..c5f106d -- code/processing python/smartdocs/worker.py
  python/smartdocs/pdf_parser.py python/smartdocs/text_parser.py resources/js/search.js`
  存在4个C++变更文件：document_search.cpp、processing_routes.cpp、processing_service.cpp/h。
  后续M3范围/外发用例补充了覆盖，但不是当前版本完整重跑M2。
- `git diff --name-only ab620f5..c5f106d -- code/agent python/smartdocs` 无差异；
  `git diff --name-only 7555af9..c5f106d -- python/smartdocs code/agent` 无差异。
  这分别支持后端M4复验和A35真实调用的核心代码关联，**不覆盖前端、依赖、部署配置或运行环境**。
  后续前端人工修订由自己的[浏览器证据](../m4/manual-revision/README.md)关联。

最终交付前应冻结发布候选，按实际差异完成当前版本的文件/解析/权限回归，
绑定受限构建的server、admin及测试二进制哈希，并完成匹配schema/配置的部署验证。
旧日志保留，不重新计算为新通过次数；重型验证继续在当前ECS按既有限额串行执行。

后续补验（源码`1b2b20f`）：[当前C++完整分组回归](cpp-regression/README.md)
逐字节核对150个相关文件、绑定上述三个二进制哈希，固定161项全部通过，0失败/跳过。
两组迁移PASS单独排除，不扩大C++分母；无新增OOM。
该结果补足当前已有C++用例的重跑证据，不改写上表历史记录，也不替代HTTP/Python/浏览器、
20轮中断、真实模型任务和部署验证。

继续补验（源码`da979e4`）：[Python及真实解析/检索HTTP](processing-regression/README.md)
分别124/124、7/7、1/1通过，0失败/跳过；绑定164个实际运行文件及server/admin哈希。
当前中文/扫描PDF、固定历史来源、重启人工重试及新版排旧链路已有新运行记录。
未包含完整M1 HTTP、浏览器、真实向量编码或常驻部署，不将这三套成绩混为业务完成率。

文件业务补验（源码`771c231`）：[当前普通版HTTP及故障版20轮](file-regression/README.md)
分别9/9场景、44断言和20/20轮、305断言通过。故障版已按当前源码增量构建，
124个相关源文件、三份二进制、完整日志和逐轮状态/SHA均绑定保存，无新增OOM。
该增量补齐现有M1 HTTP/中断断言的当前运行记录；未运行浏览器、吞吐或整机断电。
原M1证据和本轮分开，不累加为40轮，也不把HTTP内部T-07/T-09当作需求同号解析用例。

## 第13.2节固定分母与剩余交付

| 明确交付项 | 当前可核实状态 | 下一步 |
| --- | --- | --- |
| 30项固定业务任务，各类10项 | [输入/判据](../../../test/fixtures/agent-p0/tasks.json)保持冻结，历史`run_status=not_run`不回写；30/30前置装载不是任务完成。[单案例CLI](case-cli/README.md)已生成[Q08/Q09/A09实际准入拒绝证据](admission-results/README.md)，404/404/403且零模型调用，业务评审未判定。[独立30项台账](fixed-task-results.json)保留另外27项未执行状态。本地协议夹具/模拟审核不计业务完成，三项拒绝不移出分母 | 获得符合实际输出预算的限定外发/费用授权后逐项实测，按冻结场景执行必要的交错修改及真实用户确认，再做业务/全部引用评审；不自动生成授权。 |
| 20轮上传中断 | [M1原始结果](../m1/verified/results.json)及[当前源码复验](file-regression/results.json)分别20/20、305断言，六类分布均4/4/4/3/3/2，不累加分母 | 当前复验已完成；发布候选再次变化时按差异复核。不宣称1GiB/公网/整机断电成绩。 |
| 五工具至少24组 | [固定25组](../m3/mcp-contract-matrix/README.md)与[A20复验日志](../m3/provider-reasons/matrix.txt)：25/25 | 15组stdio到真实C++/SQL、5组stdio参数拒绝、5组in-process SDK发送前超时；固定分母不累计为50。 |
| 全部实际引用，少于50条时补充固定任务 | [离线判定入口](../m3/benchmark-review/README.md)和采集器已验证；尚无30项的真实输出和语义评审 | 保存每次引用及原文，全部人工检查定位与支持关系；不挑成功样本、不把50个预设依据点当50条实测引用。 |
| 日期、版本、硬件/模型/提示词/索引与实际结果 | 现有各证据目录保存独立运行的基线、环境、命令、失败和终态；真实烟测与模拟用量分开 | 正式评测保存同一次运行的这些字段、原始结果及实际usage；费用未知不能计0。 |
| 配置、样例、工具清单、部署与完整演示 | [部署手册](../../deployment/m1-single-host.md)、[工具契约](../m3/mcp-contract-matrix/README.md)、[样例](../../../test/fixtures/agent-p0/README.md)已有；阶段截图不是完整演示 | 完成匹配版本部署验证及上传→检索核查→带来源报告→预览方案→用户确认→归档的一次完整演示。当前没有生产上线或全流程真实模型演示结论。 |

100份资料、1GiB文件、4路上传等在需求第12.1节属于可按实际服务器调整的建议配置，
不是可以省略环境记录的既定成绩。已有流式测量是4×32MiB、环回网络；
正式展示需说明实际规模与限制。80%/90%也是建议目标，未测时不能填成成果。

原索引整理没有读取API key、调用模型、启动测试服务、迁移数据库或修改产品。
后续C++补验在临时隔离MySQL中运行迁移及测试，仍无API key读取、模型调用或业务库修改。
解析/检索补验同样使用隔离库和人工夹具，无API key读取、真实模型调用或业务库修改。
文件补验在受限副本增量构建故障版并用独立临时库执行HTTP/进程中断；无生产服务修改。
验收索引完成不表示T-01至T-20全部通过；未完成项仍按上述原范围交付。

本轮[只读核对输出](acceptance-audit.txt)检查20个唯一条目、20个具名测试符号、
M1结构化分母、九份历史日志的对应结果标记、真实烟测轨迹、冻结任务和上述版本差异。
最初静态检查的源码列表漏列worker测试文件，补入后20个符号均能找到；不是产品测试失败。
独立只读复核T-11至T-20的断言和记录未发现阻断项，没有代替实际执行或语义评审。
