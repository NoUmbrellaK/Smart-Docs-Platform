# M3 后端工具调用轨迹

本增量为任务搜索/读取入口增加服务端持久轨迹，不是完整模型/MCP审计或阶段验收。
`GET /api/v1/projects/{project_id}/agent-tasks/{task_id}/tool-calls`使用现有会话、项目
reader权限和本人任务限制；拒绝query和正文。每次查询重验整个任务的当前版本/删除/
具体外发批准，其他用户（包括项目管理员）不能读取他人任务轨迹。

响应data包含`kind=backend_requests`、`capacity=64`和按sequence升序的items。
每项为task_id、sequence、request_id、tool、parameters、status、started_at（UTC）、
duration_ms。单任务最多64条，因有硬上限一次返回，不提供虚假的无限分页。
达到上限后新的后端工具请求返回429 agent_audit_full，不执行读取，不自动覆盖历史。
一次MCP hybrid调用可能包含预检和最终查询，不能把本表行数等同MCP调用数；
执行核心原有12次MCP调用上限不变。

## 记录与失败语义

服务端在实际操作前提交unknown记录；操作完成后写规范化状态与耗时，成功回执
和业务错误回执携带同一request_id。首次持久化失败时不开始工具；完成记录失败时
不向调用方发送读取内容。进程在两次写之间消失时unknown保持不变，不猜测成功/失败，
也不凭它重做操作。耗时来自单调时钟，包含准入和执行，不等于供应商或完整MCP耗时。

状态区分success、processing、unavailable、invalid_request、access_denied、conflict、
busy、timeout、dependency_failed和unknown。search成功仅说明请求成功，索引降级及
覆盖仍以原搜索结果为准；read状态中的processing/unavailable不冒充已读取正文。
身份/任务不可访问、任务已终止、HTTP头/JSON解析拒绝、轨迹容量拒绝发生在准入前，
不写任务轨迹。这不是覆盖所有拒绝请求的全站安全审计。

参数摘要由C++生成，仅保存搜索mode与query_bytes、读取selector（status/chunk/range）；
不保存原查询、正文、文件名、文件ID、响应片段、密钥、Cookie或模型思维过程。
没有接受客户端上传/修改轨迹的路由，不能通过POST伪造工具成功；允许范围内的
实际HTTP调用仍会被记录，记录不是对调用方一定来自模型的证明。

## schema8和部署

新增agent_tool_calls，旧任务不回填历史。C++要求schema8，升级前停止应用/worker/
网关并备份数据库与文件，再运行scripts/migrate.sh；先升级库再换匹配二进制。
MySQL DDL非整批原子，失败应按备份恢复，不盲目重放半套DDL；回退恢复匹配备份。
只在隔离测试库验证，未迁移当前业务库，也未部署生产服务。

本轮尚未接入网页摘要展示；模型usage、Python验证失败及完整MCP耗时仍在可信CLI
本地脱敏轨迹中。当前只有search_documents/read_document_version两个工具，不能
将本接口宣称为五工具或完整任务审计已交付。
