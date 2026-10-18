# M3 MCP 只读调试入口

官方 SDK 的两个真实工具：`search_documents`、`read_document_version`。
仅为本机可信父进程启动的 stdio 调试服务，不监听 HTTP、不调用远程模型、
不提供三个写工具。不能据此宣称完整 M3、生产 Agent 或 T-18 模型出口验收完成。

## 环境与启动

当前锁定 [官方 mcp 1.30.0](https://pypi.org/project/mcp/1.30.0/)，
[官方 v1 维护分支](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
支持当前 Python3.10。完整依赖和实际 wheel SHA 见
[requirements-mcp.txt](../../python/requirements-mcp.txt)，面向 CPython3.10/Linux
x86_64；升级或换平台重新验证锁。使用独立虚拟环境，不往 worker/向量环境安装。

```bash
python3 -m venv /path/to/mcp-venv
/path/to/mcp-venv/bin/pip install --only-binary=:all: --require-hashes -r python/requirements-mcp.txt
PYTHONPATH=python /path/to/mcp-venv/bin/python -B -m smartdocs.mcp_read --context /private/context.json
```

配置文件须为当前运行用户所有的普通文件、权限精确0600、最多4096字节；
拒绝符号链接。只在安全位置由可信调试调用方创建，不提交仓库，不将 token
放命令行、模型参数或日志。字段示意（全零值为无效会话示例，不可直接登录）：

```json
{
  "backend_port": 1316,
  "origin": "http://127.0.0.1:1316",
  "session_token": "0000000000000000000000000000000000000000000000000000000000000000",
  "project_id": "00000000000000000000000000000000",
  "task_id": "00000000000000000000000000000000",
  "scope": "project_approved_versions"
}
```

scope支持两种模式：

- `task_versions`：推荐用于任务接线；task_id必须来自[C++任务创建接口](m3-agent-tasks.md)。
  任务本人、用户确认的具体版本范围、当前批准和取消状态每次由C++核验。
  两工具固定POST到`/agent-tasks/{task_id}/tools/search`或`tools/read`；失败不降级
  到全项目接口。模型不能传入task_id/user_id/project_id/批准或修改范围。
- `project_approved_versions`：上述配置示例保留的本机全项目只读调试模式；task_id
  仅关联ID，不代表持久任务，不能用于未来生产模型入口。

真实会话可撤销/过期，模型无权改变配置字段。仍无平台范围确认页面、模型出口
或完整Agent执行器，不能因新增持久任务就直接将本调试入口接远程模型使用。

## 工具参数与结果

- search_documents：必需 mode（metadata/keyword/hybrid）、query；可选
  directory_id、media_type、page（1–1000）、page_size（1–10）。
  query 最多256字符，C++还执行原有字节限制；metadata允许空查询。
  直接访问 remote-ai/search；本增量未接查询编码器，hybrid必须如实显示
  keyword_only降级，不将其计为混合检索成功。
- read_document_version：必需 file_id、version_id；可选64位小写十六进制chunk_id
  或 range 对象，两者互斥。都不提供时只返回处理状态/元数据，绝非完整正文；
  chunk_id 返回固定版本的一个精确片段；range（可为空对象）返回有界片段列表。
  range 接受 offset（默认0，0–2000）、limit（默认10，1–10），以及成对出现的
  page_start/page_end（1–300，含首尾的物理PDF页，start≤end）。页范围不能超过
  解析出的实际页数；文本文件不能按页筛选。布尔、未知字段和非法边界均拒绝。
  offset 是所选范围内按原始片段顺序跳过的片段数，不是页码或字符偏移。
- ID均为32位小写十六进制；不接受额外字段、user_id、project_id、approved、
  query_embedding或任意URL。JSON schema公开，服务端也实际验证。
- 文本中的指令仍是原文数据，不改变权限或开启写操作。仅列出上述两工具，
  execute_archive_plan等未知名称被拒绝。

MCP CallToolResult 同时提供 JSON text 与 structuredContent：

```json
{
  "request_id": "每次服务端新生成的32位ID",
  "task_id": "绑定的调试任务ID",
  "duration_ms": 12,
  "status": "success",
  "data": {}
}
```

data保留C++的出处、版本、实际检索模式、覆盖与分页标记。无权/失败结果不含
data，只有通用error.code，不回显请求参数、上游错误正文或异常。
status有success、processing、unavailable、invalid_request、access_denied、
conflict、busy、timeout、dependency_failed；只有success/processing的isError=false。
C++的pending/processing映为processing，其余未完成解析终态映为unavailable；
HTTP400/401/403/404/409/429分别映参数错误/访问拒绝/访问拒绝/访问拒绝/冲突/忙，
其他非200（包括重定向）为依赖失败。后续写工具需要增加等待确认等状态。

分页示例：`{"file_id":"…","version_id":"…","range":{"page_start":1,"page_end":3,"limit":10,"offset":0}}`。
data.chunks 最多10个原始片段，每项保留完整引用字段；data.read_range 包含
scope（document/pages）、offset、limit、content_ready、has_more、next_offset、
truncated；pages 范围另回显 page_start/page_end。has_more=true 时保持同一版本和
页范围，用 next_offset 继续；每次都重新鉴权，不缓存旧结果。chunk_count/char_count
始终指整个文档，不是所选页范围。truncated 表示当前响应未覆盖整个所选范围
（offset>0或还有下一批），不是整个对话已读内容的累计状态。

尚未解析完时 content_ready=false、chunks=[]、has_more/next_offset=null、
truncated=true，不能当作“没有内容”；此时只校验页范围语法，实际格式和页数
待解析完成后验证，不只依赖上传MIME（通用MIME也可根据扩展名交给PDF解析器）。
解析完成但结果缺失返回依赖失败。
已解析PDF的无文本页可返回空列表且content_ready=true；仍须检查pages_without_text，
它可能是空白页或未做OCR的扫描页，不能宣称资料已完整读取。

仅连接127.0.0.1的固定配置端口，不使用代理、不追随重定向。HTTP套接字超时
15秒，响应最多256KiB，不自动重试、不缓存工具结果；该socket超时不是整个
Agent任务的墙钟预算。每进程后端调用串行；stdout只写MCP协议，CLI禁用SDK
详细日志，配置/传输启动失败仅输出通用错误。

## 后续门槛

后续[只读执行核心](m3-read-agent.md)已增加任务终态、次数/字节/时间预算及模型调用前依赖复核；
仍无真实供应商接线。内部 Streamable HTTP 认证、逐次调用持久日志、用户确认范围的网页入口、查询向量接线、
三个写工具及M4事务仍待完成。一次工具读取成功不构成永久外发授权，
不能复用已经撤销批准的旧结果进入后续模型请求。本增量没有生产部署。
