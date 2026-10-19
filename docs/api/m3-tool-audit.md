# M3 后端工具调用轨迹

任务的五类后端工具入口均有服务端持久轨迹，不是完整模型/MCP审计或阶段验收。
工具为search_documents、read_document_version、create_archive_plan、execute_archive_plan、save_handover_report。
`GET /api/v1/projects/{project_id}/agent-tasks/{task_id}/tool-calls`使用现有会话、项目
reader权限和本人任务限制；拒绝query和正文。每次查询重验整个任务的当前版本/删除/
具体外发批准，其他用户（包括项目管理员）不能读取他人任务轨迹。

响应data包含`kind=backend_requests`、`capacity=64`和按sequence升序的items。
每项为task_id、sequence、request_id、tool、parameters、status、started_at（UTC）、
duration_ms。单任务最多64条，因有硬上限一次返回，不提供虚假的无限分页。
达到上限后新的后端工具请求（包括写入和幂等重放）返回429 agent_audit_full，不执行业务，
不自动覆盖历史；GET方案/归档回执/报告仍可查询，不以新操作绕过容量。
一次MCP hybrid调用可能包含预检和最终查询，不能把本表行数等同MCP调用数；
执行核心原有12次MCP调用上限不变。

## 记录与失败语义

服务端在实际操作前提交unknown记录；操作完成后写规范化状态与耗时，成功回执
和业务错误回执携带同一request_id。首次持久化失败时不开始工具；完成记录失败时
不向调用方发送成功数据，返回503且retryable=false；已提交的业务不因此回滚。
写工具发生5xx或未知异常也保留unknown，不能从连接错误推断事务未提交；调用方应查询
持久化方案、执行回执或报告，禁止自动重写。进程在两次写之间消失时unknown保持不变，不猜测成功/失败，
也不凭它重做操作。耗时来自单调时钟，包含准入和执行，不等于供应商或完整MCP耗时。

状态区分success、processing、unavailable、invalid_request、access_denied、conflict、
busy、timeout、dependency_failed、waiting_confirmation和unknown。waiting_confirmation仅说明
方案已保存且等待真人确认，不表示文件已移动。search成功仅说明请求成功，索引降级及
覆盖仍以原搜索结果为准；read状态中的processing/unavailable不冒充已读取正文。
身份/任务不可访问、只读工具任务已终止、HTTP头/JSON解析拒绝、轨迹容量拒绝发生在准入前，
不写任务轨迹。这不是覆盖所有拒绝请求的全站安全审计。
写请求允许进入轨迹后再由业务层检查状态，以覆盖明确拒绝与已完成操作的幂等重放；
真人确认/拒绝不是MCP工具调用，不额外制造工具轨迹，仍有原业务审计。

参数摘要由C++生成，仅保存搜索mode与query_bytes、读取selector（status/chunk/range）；
写摘要仅允许item_count及有效非负整数revision/expected_revision/policy_revision；
不保存原查询、正文、文件名、文件ID、归档理由、operation_id、响应片段、密钥、Cookie或模型思维过程。
写工具不写来源回执source_json/model_call，不能成为模型引用来源；原读工具来源台账不变。
没有接受客户端上传/修改轨迹的路由，不能通过POST伪造工具成功；允许范围内的
实际HTTP调用仍会被记录，记录不是对调用方一定来自模型的证明。

## schema16和部署

schema8新增agent_tool_calls；schema16扩展同表工具名和状态CHECK，保留已有记录，
不回填历史写请求、不新增表。当前C++要求schema16，升级前停止应用/worker/
网关并备份数据库与文件，再运行scripts/migrate.sh；先升级库再换匹配二进制。
MySQL DDL非整批原子，失败应按备份恢复，不盲目重放半套DDL；回退恢复匹配备份。
只在隔离测试库验证，未迁移当前业务库，也未部署生产服务。

现已接入[任务网页的手动摘要展示](m3-agent-workspace.md)；模型usage、Python验证失败及完整MCP耗时仍在可信CLI
本地脱敏轨迹中。网页提供五工具名称、脱敏参数与状态的手动展开，不发起写操作。
不能将这五类后端请求记录宣称为完整MCP/模型审计已交付。A18验证见
[证据记录](../evidence/m3/write-tool-audit/README.md)。
