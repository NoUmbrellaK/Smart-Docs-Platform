# M3 归档确认、整批执行与回执

方案A的归档业务基础。C++是权限和写状态权威；尚未发布MCP写工具。
Agent页面现支持查看具体修订、真实确认/拒绝及独立执行，不会自动确认、执行或调用模型。

所有路径前缀为 `/api/v1/projects/{project_id}/agent-tasks/{task_id}/archive-plan`。
沿用登录会话；只能操作本人任务（管理员也不能代看其他人的任务）。POST要求同源Origin和
当前项目编辑/管理员权限，JSON最大4KiB，严格字段/类型校验；GET要求当前项目成员且无请求体。
拒绝查询参数。正常返回 `{request_id,data}`，错误不包含文件名、目录名或理由。

## 等待与确认

保存新预览后任务进入 `waiting_confirmation`，释放运行槽。已有running任务超过180秒预算后
不能发布方案。等待不重置model_calls或started_at；不会自动继续模型调用。两种等待状态均可取消，
服务重启保留等待状态；只有running按既有规则变为interrupted。

GET/PUT预览增加 `content_sha256`、`decision`（null/confirm/reject）、`operation_id`（默认null）、
`task_state`。摘要来自服务端规范化存储的完整预览，绑定计划ID、修订、文件版本、名称、源/目标、
理由和策略修订；动态状态不参与摘要。客户端不得自造摘要。预览状态为preview/confirmed/rejected/executed。

POST `/decision`：

```json
{"plan_id":"0123456789abcdef0123456789abcdef","revision":1,"content_sha256":"替换为GET返回的64位摘要","decision":"confirm"}
```

decision也可为reject。确认前重验完整任务范围及方案快照；含待定目标的方案不能确认。
确认记录绑定本人、计划、修订和摘要，并由服务器生成唯一operation_id；任务变为waiting_execution。
确认本身不移动文件。拒绝保留waiting_confirmation，且该修订不能再确认；用户须显式修订再确认。
同决定重放不重复审计；同修订相反决定409。显式新修订作废旧确认与旧操作ID，不能修改已执行计划。
`confirm_remote_ai`、模型文本“已批准”或未知的approved字段均不能代替此确认记录。

## 执行与原子性

POST `/execute`：

```json
{"plan_id":"0123456789abcdef0123456789abcdef","revision":1,"operation_id":"替换为确认返回的32位操作ID"}
```

首次执行要求当前修订、有效确认及waiting_execution，重新检查角色、整个任务的版本/批准/可用性、
所有源位置/名称、目标策略修订/批准/名称、同名冲突及取消状态。锁顺序沿用成员→队列/任务→
按ID排序的全范围文件独占锁→计划/确认→策略/目录。与取消争用同一任务锁，提交先后决定结果。
所有文件逻辑位置、执行结果、archive.execute审计和任务completed在同一MySQL事务提交；
不调用每文件独立提交的公开移动接口，不覆盖原文、不新建版本、不移动物理对象。
中途数据库错误回滚全部业务结果；不会自动重试写操作。

执行返回operation_id、plan_id、revision、status=executed、moved_count、audit_id和items。
逐项仅含稳定file_id/version_id/source_directory_id/target_directory_id及state=moved，无名称或理由。
重复或并发相同执行返回持久原结果，不再移动、不新建审计；仍须本人、当前编辑权限和完整范围有效。
重放不要求文件仍位于旧源位置，不会把后来人工移动的文件移回目标。

## 丢失响应后的安全恢复

GET `/result`返回最小持久回执（无结果404）：
operation_id、plan_id、revision、status、moved_count、audit_id，共六字段。
它只要求本人及当前项目成员，不重新要求文件外发批准，因为没有文件ID、名称、理由或正文。
因此提交后资料撤销仍可确认“操作曾提交”，但不能读取完整旧方案或借重放绕过当前权限。
回执不是文件当前所在位置的承诺。退出项目或他人查询仍被拒绝。

任务GET `/run/result`对已提交归档返回kind=archive_operation、result=null、error_code=null、
archive_operation=上述最小回执、原model_calls（未调用则0）。不是模型回答或已保存交接报告。

## schema10和部署

010_archive_execution.sql扩大任务状态字段并新增archive_decisions；不回填历史确认、操作ID或回执。
复合外键将确认的task_id/plan_id绑定同一方案，数据库也拒绝跨任务错配记录。
schema9旧预览仍可查看并显式确认，不能因升级自动执行。此为schema10历史增量；当前二进制要求
[schema11](m3-handover-checklist.md#schema11和部署)。部署前停应用/worker/网关，备份数据库及文件，
再执行scripts/migrate.sh；当前分支预期schema_version=11。
MySQL DDL并非整批原子：失败应按匹配备份恢复，不盲目重放半套DDL。
本轮仅迁移独立临时测试库，没有授权或执行生产迁移/部署，也没有远程模型调用。

## 网页确认与恢复

在本人任务详情点击 **View archive plan**，按需读取权威方案和当前项目目录，显示方案ID、
修订、摘要，以及每个文件的名称、固定版本、源目录、目标目录和理由。普通目录接口提供的
源目录名称只在本地页面展示，不因此成为允许外发给模型的上下文。全部文字通过textContent
渲染；文档或理由里的“已批准”不能设置勾选状态或生成确认记录。

当前编辑者/管理员查看waiting_confirmation的preview时，必须勾选默认未选中的同修订确认框，
再点击 **Confirm this revision**；请求只携带服务端方案ID/修订/摘要和confirm。**Reject this revision**
独立记录reject，不需要勾选。待定目标只能拒绝、不能确认；已拒绝修订不能就地改为确认，须由
提案接口生成新修订。本增量没有新增方案编辑器或模型修订入口。

confirmed且waiting_execution、executable为真时才显示 **Execute confirmed revision**，发送该
修订和服务器返回的operation_id。确认响应不触发执行；执行成功仅展示固定六字段历史回执，
不显示任意items或将回执描述为文件现位置。任务列表仍是快照，需显式刷新。

**Check archive receipt** 是独立只读恢复入口。方案读取失败、写入冲突或响应丢失会清掉旧方案
及动作，提示重新核对；不自动重放写入。即使整个方案因版本批准撤销不可读，仍可按回执接口
规则查询历史提交。项目/本人角色/任务变更与注销均使旧按钮和迟到响应失效；在途写锁直到
原请求返回才释放，页面失效不会允许重复点击并发写入。读取者和终止任务没有归档写按钮，
最终授权仍由后端每次重新校验。此锁不跨标签页；跨页面重复操作依赖后端幂等与修订绑定。

## 剩余边界

MCP写工具及模型等待/继续编排、个人交接报告和五工具联调尚未交付。
网页验证的提案是夹具，不代表模型分类质量；见[确认网页证据](../evidence/m3/archive-ui/README.md)。
完整目标和M3/M4均未关闭。
