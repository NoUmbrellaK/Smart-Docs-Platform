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

scope 明确为所绑定项目的获批版本，不支持文件子集。task_id 是可信父进程的
关联 ID，不代表已创建持久 Agent 任务。真实会话可撤销/过期，每次请求仍由
C++ 重新认证、校验项目权限和指定版本外发批准。模型无权改变任何配置字段。
本调试入口尚无平台范围确认界面，不能直接接远程模型使用。

## 工具参数与结果

- search_documents：必需 mode（metadata/keyword/hybrid）、query；可选
  directory_id、media_type、page（1–1000）、page_size（1–10）。
  query 最多256字符，C++还执行原有字节限制；metadata允许空查询。
  直接访问 remote-ai/search；本增量未接查询编码器，hybrid必须如实显示
  keyword_only降级，不将其计为混合检索成功。
- read_document_version：必需 file_id、version_id；可选64位小写十六进制chunk_id。
  无chunk_id只返回版本处理状态/元数据，绝非完整正文；有chunk_id返回该固定
  版本的一个精确片段。页范围、连续片段范围及整篇分页还未实现。
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

仅连接127.0.0.1的固定配置端口，不使用代理、不追随重定向。HTTP套接字超时
15秒，响应最多256KiB，不自动重试、不缓存工具结果；该socket超时不是整个
Agent任务的墙钟预算。每进程后端调用串行；stdout只写MCP协议，CLI禁用SDK
详细日志，配置/传输启动失败仅输出通用错误。

## 后续门槛

内部 Streamable HTTP 认证接线、持久任务/取消/预算、用户确认的允许版本范围、
每次工具返回和模型出口的上下文依赖复核、页/片段范围读取、查询向量接线、
三个写工具及M4事务仍待完成。一次工具读取成功不构成永久外发授权，
不能复用已经撤销批准的旧结果进入后续模型请求。本增量没有生产部署。
