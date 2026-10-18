# M3 MCP 只读工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 用官方 SDK 提供可通过真实客户端独立验证的只读工具调试入口。

**Architecture:** 一个 Python 模块绑定可信调用上下文并调用既有 C++ remote-ai 路径；协议、schema 校验复用 SDK/jsonschema。父进程启动 stdio，无监听端口。

**Tech Stack:** Python3.10、mcp1.30.0、stdlib HTTP、现有 unittest/真实 C++ MySQL测试设施。

**Spec:** docs/superpowers/specs/2026-10-18-m3-mcp-read-tools-design.md

## Global Constraints

- 不改变 C++ 权限，不允许模型参数指定身份/项目/批准。
- 最多10项、256KiB响应、15秒HTTP超时；无模型调用、自动重试或结果缓存。
- 复用现有限资源单元串行验证；不更改生产环境。

## Task 1: 两项只读工具、私有 stdio 入口及证据

**Files:** python/smartdocs/mcp_read.py、python/requirements-mcp.txt、test/python/test_mcp_read.py、test/e2e/m3_mcp_read_test.py、docs/api/m3-mcp-read-tools.md、README.md。

**Interfaces:** create_server(context) 返回官方 Server；context包含backend_port/origin/session_token/project_id/task_id/scope。main通过唯一配置路径读取该上下文并调用SDK stdio_server。工具调用返回CallToolResult，structuredContent含request_id/task_id/duration_ms/status/data或error。

- [ ] 从官方PyPI下载兼容wheel，生成固定版本SHA锁文件并在独立临时环境离线hash检查安装；运行既有Python基线。
- [ ] 编写test_mcp_read：缺失模块应通过assertIsNotNone(find_spec(...))明确失败；用SDK ClientSession + 官方内存管道进行initialize/list_tools/call_tool；确认额外user_id被拒绝且HTTP接收器无请求。
  ```python
  result = await session.call_tool('search_documents', {'mode':'metadata','query':''})
  assert result.structuredContent['status'] == 'success'
  assert result.structuredContent['data']['items'] == []
  ```
- [ ] 用固定JSON schema与Server.list_tools/call_tool实现模块；HTTPConnection只请求127.0.0.1 remote-ai接口，限定响应并脱敏失败。配置文件os.open(O_NOFOLLOW)+fstat验证普通文件、所有者、0600、大小上限后读取。
- [ ] 通过协议测试（真实HTTP接收器仅用于故障注入）；补参数/身份/重定向/错误/响应限制/配置权限用例，全部实际运行。
- [ ] 集成测试复用m1_http_test与m2_worker_test设施，通过真实上传/审批/worker后以SDK stdio客户端调用；验证内部文件不出现、允许片段一致、审批撤销后拒绝、未知写工具不可用。
- [ ] 运行完整Python回归、独立只读审查、检查临时单元退出/原MySQL正常；记录实际版本、失败与未验证边界，更新README并正常提交推送。
