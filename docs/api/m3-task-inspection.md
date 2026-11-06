# M3 任务检查限制

本接口为新任务提供显式、不可变的检查限制。它不从问题、文档内容或模型输出推断授权，
不改变历史任务，也不表示原自然语言 H05 或业务验收已经通过。

## 创建与返回

`POST /api/v1/projects/{project_id}/agent-tasks` 可选增加：

```json
{"inspection_constraints":{"mode":"metadata","max_page":1,"page_size":10}}
```

省略字段时保持普通任务的既有行为。显式 `null`、未知或缺失子字段、布尔、浮点和错误范围均返回
`400 invalid_request`。`mode` 只允许 `metadata`；`max_page` 为 1–1000 的整数，`page_size`
为 1–10 的整数。该字段只在创建时接受，不能更新；普通问答、交接报告和归档方案任务均可携带。

受限任务的 Create、Get、List 和 `run/start` 响应返回同一结构化
`inspection_constraints`。未受限任务不返回该字段。约束保存在任务行中，重启后不变。

## C++ 权威边界

服务端在既有本人任务、项目成员、固定版本、外发批准和任务状态检查之后，于同一事务和锁内执行限制：

- 只允许 `search_documents` 的 `metadata` 模式；有效 `page` 不得大于 `max_page`，有效
  `page_size` 不得大于任务上限。
- 搜索仍沿用既有默认 `page=1`、`page_size=10`。任务上限小于 10 时，调用方必须明确提交较小
  `page_size`，否则默认值也会被拒绝。
- `read_document_version` 的状态、chunk 和 range 三种选择器全部拒绝。
- 拒绝返回 `403 task_inspection_restricted`，不带资料正文；既有工具审计把调用记录为
  `access_denied`。

Python 动态工具限制只能减少无效选择，不能代替此服务端检查；直接 HTTP 或 MCP 调用不能绕过。
普通文件查看和下载不受影响，保存交接报告与创建归档方案也保持原语义。选择 metadata 不等于确认归档。

## Python 运行器

运行器把 `run/start` 返回的约束视为服务端可信控制信息，但仍在任何模型调用前严格校验其完整形状、
整数类型和范围。有限制的每次运行都从全局 `SCHEMAS` 深拷贝工具 schema，再执行以下收窄；全局
schema 和下一次普通任务不会被修改：

- 不向模型提供 `read_document_version`。
- `search_documents.mode` 固定为 `metadata`。
- `page.maximum` 和 `page_size.maximum` 使用任务保存的上限，并把 `page_size` 设为必填。
- 原有终止工具保持可用：交接任务仍可保存私人报告草稿，归档任务仍只能创建待用户决定的方案。

运行器另以 system 消息传递结构化可信约束，要求保留未读正文造成的不确定性。模型即使尝试调用已隐藏
的读取工具，也只会得到本地 `unknown_tool` 拒绝，不会派发到 MCP。服务端限制仍是最终权威。

## 网页选择

任务表单提供默认未选中的原生 `Metadata search only` 复选框；启用后默认最大页数 1、每页上限 10，
两项数字输入分别只接受 1–1000 和 1–10 的整数。修改开关或任一上限都会撤销外发确认，用户必须重新
确认后才能创建。创建请求进行中会锁定这些控件；新草稿和切换项目会恢复未选择的 1/10 默认值。

未选择时创建 payload 不包含 `inspection_constraints`。选择且通过本地校验时只发送结构化字段，不解析
问题文字。任务详情从服务端返回值用 `textContent` 显示持久化限制。表单在窄屏保持单列，数字输入和
操作控件保留至少 44px 触控高度。

`max_page` 和 `page_size` 是每次搜索请求的数值边界，不是跨查询的累计结果预算。调用方可以用不同
metadata 查询或过滤条件得到不同的第一页；因此不能声称限制为 1 页、每页 10 项就代表最多能看到
10 个唯一记录，也不能据此宣称语义上的完整检查。

## schema17

`017_task_inspection_constraints.sql` 为 `agent_tasks` 增加可空 JSON 列和数据库形状约束。
升级前已有任务保持 `NULL`，不回填选择或同意。新 C++ 启动及 READY 检查要求 schema17。
正式升级应先停应用、worker 和网关并备份，运行 `scripts/migrate.sh`，确认
`schema_version=17` 后再启动匹配版本。MySQL DDL 与版本记录不是单个原子事务；部分失败时应恢复
匹配备份后重试，不应只修改版本记录。本轮验证只使用隔离测试库，不触碰生产数据库或服务。
