# M3 归档目标策略与方案预览

方案A已于2026-10-19获用户批准。此接口是C++业务基础，后续已接入[归档MCP工具](m3-archive-mcp.md)，
预览本身不调用模型或移动文件；后续新增的[确认/执行接口](m3-archive-execution.md)独立授权。
确认网页及归档MCP工具已接入，归档模型编排仍未完成。

所有接口使用已有会话和项目角色。PUT要求同源Origin；拒绝查询参数、未知JSON字段，
GET不接受请求体。请求最大64KiB，错误不返回其他项目的文件名或目录名。
返回包装仍为 `{request_id,data}`；保存和对应业务审计在一个事务提交，审计关联request_id。

## 允许归档的目标目录

GET/PUT `/api/v1/projects/{project_id}/archive-policy`。
GET要求项目成员，PUT仅管理员。未设置时返回revision=0、targets=[]、remote_ai_approved=false。
PUT示例（ID替换为真实同项目目录，名称必须与用户所见当前名称一致）：

```json
{
  "expected_revision": 0,
  "targets": [{"directory_id": "0123456789abcdef0123456789abcdef", "name": "部署资料"}],
  "confirm_remote_ai": true
}
```

返回 `{revision:1, targets:[...], remote_ai_approved:true}`。最多100个不重复目录，
包括已有根目录也须显式列入。允许空白名单。confirm_remote_ai=false可保存内部配置或撤销批准。
这是目录元数据批准，不是文件移动确认，也不是文件版本外发批准。
同旧修订、同请求的重放返回原修订；不同请求或过期修订409，不静默覆盖管理员配置。

目标目录实际改名会撤销整份策略外发批准并增加修订；改回原名也不恢复批准。
无变化的改名不撤销，非目标目录的改名不影响策略。管理员须按当前名称重新提交新修订。
自动撤销与directory.rename在同一事务，由该改名审计追溯；不伪造用户重新保存策略的记录。
GET策略供项目成员管理使用，可包含未批准的名称；未来模型上下文必须走
ApprovedArchivePolicy的批准/修订/当前名称校验，不能直接转发普通GET结果或完整目录列表。

## 本人任务的归档方案

GET/PUT `/api/v1/projects/{project_id}/agent-tasks/{task_id}/archive-plan`。
PUT要求编辑/管理员且任务属于本人、状态为pending/running/waiting_confirmation/waiting_execution；GET要求当前项目成员且属于本人。
管理员不能查看其他人的方案。GET可读已取消/中断/结束任务保存的方案，但仍执行全部数据校验。

```json
{
  "expected_revision": 0,
  "policy_revision": 1,
  "items": [{
    "file_id": "11111111111111111111111111111111",
    "version_id": "22222222222222222222222222222222",
    "source_directory_id": "33333333333333333333333333333333",
    "target_directory_id": "0123456789abcdef0123456789abcdef",
    "reason": "与部署步骤相关，建议归入部署资料。"
  }]
}
```

每任务一个稳定plan id；显式修订绑定expected_revision，修订递增。同旧修订/同完整请求
重放返回原方案内容和当前决定/任务状态、无重复审计；同旧修订不同内容409。数组顺序属于请求内容；不会自动创建新计划绕过冲突。
最多100个唯一文件，reason非空且最多1000 UTF-8字节。revision参数为0至4294967294的整数，
拒绝布尔/浮点/字符串；target_directory_id可为null，表示待确认。

返回字段：id、task_id、project_id、revision、policy_revision、state="preview"、
executable=false、has_unresolved，以及items。逐项包含请求字段及服务端名称name、
已批准目标名称target_name（待确认时null）、state="proposed"或"needs_review"。
不返回源目录名称，避免把未经批准的目录元数据混入未来模型工具结果。
schema10起另返回摘要、决定、操作ID和任务状态；只有当前已确认且等待执行时executable=true，
详见[确认与回执](m3-archive-execution.md)。保存新修订进入waiting_confirmation并作废旧确认。

保存和查询均重验任务的整个固定版本范围，包括未列入方案的文件；还检查当前文件位置/名称、
策略修订和目标名称。源版本失效、权限/外发撤销、目录改名、源位置变化或新同名冲突后拒绝旧预览。
已提交方案GET仍重验整个范围及目标批准，但不要求旧源位置（归档已改变位置），明确标注executed。
首次生成核对请求源位置；后续显式修订可以根据最新文件位置重新提案。
同目录目标拒绝；目标现存同名文件或批内目标同名均409，使用MySQL与文件唯一约束一致的
大小写/重音排序规则。即使某占位文件也拟移走，首版仍保守拒绝，不自动覆盖或重命名。

主要错误：400 invalid_request；401会话无效；403 forbidden；404 resource_not_found；
409 archive_policy_changed / archive_plan_changed / name_conflict / task_scope_changed / task_inactive。
错误后客户端不得继续展示旧缓存为当前有效方案。

## schema9和部署

009_archive_previews.sql只新增archive_policies和archive_plans，不回填授权或方案。
此为schema9历史迁移；当前C++要求[schema14](m3-handover-report.md#schema14和部署)。部署前停应用/worker/网关并备份数据库和
文件，运行scripts/migrate.sh后再启用匹配二进制。MySQL DDL不是整批原子操作；失败按匹配备份恢复，
不能盲目重放半套DDL。本轮只允许在独立临时数据库验证，不自动迁移业务库或部署生产服务。

## 尚未交付

真实网页确认/拒绝、清单/个人报告后端和可信来源依赖已在后续增量交付；
归档模型上下文及等待/继续编排、个人报告网页和五工具整体验收仍待完成。
预览历史验证见[首个增量证据](../evidence/m3/archive-preview/README.md)；它不证明后续执行事务已经验收。
