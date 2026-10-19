# 方案A：归档确认和原子执行

承接已批准的write-boundary-audit及schema9预览；仅实现同项目已有逻辑目录的归档。
此轮是C++业务核，不发布MCP写工具、不自动调用模型、不部署生产。

## 确认与状态

- 新增schema10的archive_decisions，主键(task_id,revision)，绑定plan_id、内容SHA256、
  当前用户、confirm/reject、时间；确认才生成持久唯一operation_id。执行结果也存本行。
  迁移不回填任何确认、操作或结果。旧预览仍需用户重新查看并显式确认。
- SaveArchivePlan保持无移动，但将任务置waiting_confirmation，释放running槽。
  首次保存/修订以及确认均重验全范围。正在running的模型任务若180秒已耗尽则不能保存方案。
  waiting_confirmation/waiting_execution允许显式修订；已执行方案禁止再修改。
- GET预览增加content_sha256、decision、operation_id和task_state；state为preview/confirmed/rejected/executed。
  摘要由规范化存储的preview_json计算，绑定计划ID、修订、所有条目和策略修订，不由模型提供内容替换。
- POST `.../archive-plan/decision` 严格接受 `{plan_id,revision,content_sha256,decision}`，
  decision仅confirm/reject；本人编辑/管理员、同源、当前有效预览且修订/摘要一致。
  有待确认目标时不能confirm。相同决定重放返回原记录；同修订相反决定409，撤销可取消任务或显式改方案。
  confirm使任务waiting_execution；reject保留waiting_confirmation但该修订不可执行。
  新修订使旧确认无效，不能用老operation_id执行新方案。
- POST `.../archive-plan/execute` 严格接受 `{plan_id,revision,operation_id}`。
  这是用户确认后明确继续的动作，不自动继续模型。MCP没有确认接口，approved字段不能产生授权。
  网页确认按钮及完整交互留给后续M4增量；当前真实HTTP验证不冒充真人网页验收。

## 原子性、重试和访问

- 持有成员→队列/任务锁；首次执行按文件ID顺序锁整个来源范围文件FOR UPDATE，
  再校验具体版本批准；与预览一致，之后锁方案/决定→项目策略→目录和冲突行。
  取消锁同一任务行：取消先提交则无新移动，执行先提交则取消不能撤销已提交结果。
- 首次执行须waiting_execution、有匹配本人确认、有效当前修订/摘要、全范围版本/权限/外发、
  源位置/名称、目标批准/名称/同名冲突全部有效。整批UPDATE files、持久结果、业务审计同事务提交。
  失败整批回滚，保留原确认供核对；不自动重试写操作，不调用逐文件独立事务API。
- 成功任务completed，结果类型archive_operation，不伪造模型回答；model_calls保持原累计值。
  已执行方案不可修改。重复执行仅返回既有结果，不重新检查旧源位置、不再次移动或增加审计。
  POST重放仍需编辑权限及全任务版本外发校验；若来源已失效，可走下述最小回执查询，不能强行重做。
- GET `.../archive-plan/result` 在本人项目阅读权限下返回已提交最小回执：操作ID、方案ID、
  修订、status、moved_count、audit_id。无文件名/原文/理由/路径/文件条目，不依赖仍有效的旧源位置。
  因此取消/后续更新后仍可定位历史是否提交；无结果返回404。完整方案仍重验全范围与策略。
  执行响应还返回逐项稳定ID移动结果；不得转发失效来源给后续模型，未来MCP出口另需重验。
- 运行结果API识别archive_operation；网页只补正确状态、等待时取消和最小回执展示，复用原按钮与布局。
  不把归档结果渲染成“回答草稿”，不显示自动执行或自动恢复按钮。
- waiting状态重启后保留，running仍标interrupted。旧执行器不能在waiting状态继续/check/finish。
  本轮不恢复模型循环、不重置model_calls或started_at；后续有界编排接入时单独验收累计时间/工具预算。

## 验证与环境

真实HTTP/MySQL验证未确认、摘要/修订不匹配、拒绝、改方案、角色下降、目标改名、
文件变更、取消先后、并发重复执行、重启、丢弃响应体后重放；临时库触发器在第二个移动且
第一个已执行时注入SQL异常，验证文件位置、结果及审计全部回滚。前端回归及六屏宽浏览器检查。
继续CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、磁盘双向5MB/s/200IOPS，
单线程构建和串行测试；MemAvailable低于512MiB停止本轮测试。只迁移临时库，无模型外发。
