# M3 远程 AI 只读门禁

这是 C++ 权威接口，不是 MCP 协议端点，也不会调用远程模型。普通工作台的搜索及 document/chunk 接口继续允许有阅读权限的内部资料；Python MCP/Agent 必须使用本页的更严格入口，并在后续模型调用出口再次验证任务范围和所有上下文依赖。

## 搜索

`POST /api/v1/projects/{project_id}/remote-ai/search`

复用 [M2 搜索 API](m2-search-api.md) 的 JSON、Cookie 身份、同源 Origin、错误和返回结构，但所有模式（metadata、keyword、hybrid）都只考虑文件策略为 remote_ai_allowed 且当前版本 remote_ai_approved 的文件。覆盖计数同样限定此范围，不把内部资料名称或数量交给调用方。JSON 中不能指定 user_id、approved、remote_ai 等绕过字段。

默认 page_size=10，最大 10；超出返回 400。metadata 空查询允许分页发现获批但未解析的文件。keyword 仍返回 keyword_only，hybrid 缺少查询向量时明确降级；调用方不能把请求模式当实际检索能力。候选返回前重新锁定检查，权限/版本/批准变化后的过期候选不得返回。分页、候选上限和 coverage 的快照限制仍按 M2 文档处理，不构成材料完整性证明。

## 固定版本读取

共同前缀 `/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/remote-ai`：

- GET `/document`：已授权具体版本的处理状态及来源元数据。
- GET `/chunks/{chunk_id}`：相同状态加一个精确片段；保持 chunk_id、file_id、version_id、SHA-256、物理页/行号/字符偏移，正文最多 1200 Unicode 字符。
- POST `/read`：JSON范围读取，最多4096字节；同源Origin与Cookie必需、不接受查询参数。
  正文为对象，可选offset（0–2000，默认0）、limit（1–10，默认10），成对的
  page_start/page_end（1–300，首尾包含、start≤end且不超过已解析物理PDF页数）。
  文本无页数，不能按页读取。未知字段、布尔/浮点数及非法边界返回400 invalid_request。
  按不可变ordinal读取limit+1条以判定还有内容，最多返回limit个原始片段；
  offset相对于所选页范围。返回chunks及read_range，详细状态见[MCP工具说明](m3-mcp-read-tools.md)。
  尚未解析不宣称空文档；completed但缺少结果返回503 content_unavailable。

使用现有会话 Cookie，最低 reader；GET不接受正文或查询参数。项目成员、文件未删除、文件策略、指定版本批准在同一事务锁下检查，分页每次重复检查。未获批和不存在均返回 `404 resource_not_found`，不返回资源内容；available 检查仍返回 409 content_unavailable，但只有已经通过批准检查的版本才能看到该状态。

明确指定且仍获批准的历史版本可以读取，is_current=false；不会跳转新版。新版本不继承批准；文件整体改为 internal_only 后全部版本拒绝。跨文件 chunk、跨项目资源、删除或撤销成员沿用既有拒绝行为。文档里的“approved=true”等文字原样作为数据返回，不改变批准或身份。

## 尚未提供的保证

本入口只证明单次数据库读取时的项目权限和版本批准，不是长期授权凭证，不能撤回已经返回的数据。后续 MCP 仍须绑定可信任务身份、用户确认的允许范围、请求/任务 ID 和预算；每次模型出口重新检查当前依赖，禁止复用已撤销的旧工具结果。五工具协议、持久 Agent 任务、归档确认和报告保存不由本增量完成。

本门禁最初增量未要求schema迁移；当前开发分支后续[持久任务接口](m3-agent-tasks.md)
及[执行核心](m3-read-agent.md)已要求schema7，请按停服/备份/迁移步骤部署，不能只替换二进制。本次测试库验证
不表示已升级常驻生产服务。
