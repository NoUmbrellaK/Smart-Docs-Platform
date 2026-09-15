# M1 HTTP API

本文记录当前 C++ 服务实现的 M1 HTTP/1.1 接口。所有业务 API 使用 `/api/v1`，浏览器页面与 API 同源部署。M1 只创建 `pending` 文档处理任务，不提供解析、索引、搜索、Agent 或 MCP 接口。

## 通用约定

除健康检查外，接口使用 `smartdocs_session` Cookie 鉴权，不接受客户端在 JSON 中传入用户身份。登录成功的 Cookie 包含 `HttpOnly; SameSite=Strict; Path=/`；生产配置额外包含 `Secure`。

所有修改状态的请求必须同时包含 `Host` 和完全匹配的 `Origin`。当 `SMARTDOCS_SECURE_COOKIE=true` 时，期望值为 `https://<Host>`；否则为 `http://<Host>`。服务不开放跨域凭据。

普通 JSON 成功响应：

```json
{
  "data": {},
  "request_id": "0123456789abcdef0123456789abcdef"
}
```

`204` 响应没有正文，在 `X-Request-ID` 返回请求 ID。文件响应是原始字节并在同一响应头返回 `X-Request-ID`。所有响应包含 `X-Content-Type-Options: nosniff`。

统一错误响应：

```json
{
  "error": {
    "code": "version_conflict",
    "message": "the file current version has changed",
    "retryable": false
  },
  "request_id": "0123456789abcdef0123456789abcdef"
}
```

项目、用户、目录、文件、版本和任务 ID 均为 32 位小写十六进制；SHA-256 为 64 位小写十六进制。客户端不能制造或替换服务端身份字段。

## 角色矩阵

| 操作 | admin | editor | reader |
| --- | --- | --- | --- |
| 查看目录、文件、版本、任务和下载 | 允许 | 允许 | 允许 |
| 上传文件/新版本，重命名、移动、删除、还原 | 允许 | 允许 | 拒绝 |
| 创建/重命名目录 | 允许 | 拒绝 | 拒绝 |
| 查看/修改成员，维护远程 AI 策略 | 允许 | 拒绝 | 拒绝 |

上传任务还绑定创建用户；同项目其他成员不能查询、续传、完成或取消该任务。不存在和不能向调用者泄露的资源统一返回 `404 resource_not_found`。

## 路由总览

### 健康、身份与项目

| 方法与路径 | 权限 | 请求 | 成功 |
| --- | --- | --- | --- |
| `GET /api/v1/health/live` | 无 | 无正文 | `200 {status:"live"}` |
| `GET /api/v1/health/ready` | 无 | 无正文 | `200 {status:"ready"}`，未就绪为 503 |
| `POST /api/v1/auth/login` | 无 | `{"username":string,"password":string}` | `200`，设置会话 Cookie，返回用户、过期时间和项目列表 |
| `POST /api/v1/auth/logout` | 已登录 | JSON 空对象 `{}` | `204`，撤销并清空 Cookie |
| `GET /api/v1/me` | 已登录 | 无正文 | `200`，用户、会话过期时间和项目列表 |
| `POST /api/v1/projects` | 已登录 | `{"name":string}` | `201`，调用者成为 admin |
| `GET /api/v1/projects/{project_id}/members` | admin | 无正文 | `200 {items:[...]}` |
| `PUT /api/v1/projects/{project_id}/members/{user_id}` | admin | `{"role":"admin|editor|reader"}` | `200` |

项目列表项为：

```json
{
  "id": "<project_id>",
  "name": "Design",
  "root_directory_id": "<directory_id>",
  "role": "admin"
}
```

成员项为 `{"user_id":"...","username":"...","role":"reader"}`。项目和目录名称会去除首尾空白，长度必须为 1–255，不能包含 `/` 或 NUL。

### 目录与文件

| 方法与路径 | 权限 | 请求 | 成功 |
| --- | --- | --- | --- |
| `GET /api/v1/projects/{project_id}/directories` | member | 无正文 | `200 {items:[Directory]}` |
| `POST /api/v1/projects/{project_id}/directories` | admin | `{"parent_id":"...","name":"..."}` | `201 Directory` |
| `PATCH /api/v1/projects/{project_id}/directories/{directory_id}` | admin | `{"name":"..."}` | `200 Directory` |
| `GET /api/v1/projects/{project_id}/files` | member | 查询参数见下文 | `200 Page<File>` |
| `GET /api/v1/projects/{project_id}/files/{file_id}/versions` | member | 无正文 | `200 {items:[Version]}` |
| `PATCH /api/v1/projects/{project_id}/files/{file_id}` | admin/editor | `{"name":"...","directory_id":"..."}` | `200 File` |
| `DELETE /api/v1/projects/{project_id}/files/{file_id}` | admin/editor | 无正文 | `204` |
| `POST /api/v1/projects/{project_id}/files/{file_id}/restore` | admin/editor | 空正文 | `200 File` |
| `PUT /api/v1/projects/{project_id}/files/{file_id}/remote-ai-policy` | admin | `{"policy":"...","approved_version_ids":[...]}` | `200` 当前策略 |
| `GET /api/v1/projects/{project_id}/files/{file_id}/content` | member | 可选单段 `Range` | `200` 或 `206` 字节流 |
| `GET /api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/content` | member | 可选单段 `Range` | `200` 或 `206` 字节流 |

目录对象：

```json
{"id":"...","project_id":"...","parent_id":null,"name":"/"}
```

文件对象：

```json
{
  "id": "<file_id>",
  "project_id": "<project_id>",
  "directory_id": "<directory_id>",
  "name": "spec.pdf",
  "current_version_id": "<version_id>",
  "deleted": false,
  "remote_ai_policy": "internal_only"
}
```

版本对象：

```json
{
  "id": "<version_id>",
  "file_id": "<file_id>",
  "version_number": 1,
  "size": 12345,
  "sha256": "<64 lowercase hex>",
  "media_type": "application/pdf",
  "processing_state": "pending",
  "remote_ai_approved": false
}
```

文件列表支持唯一参数 `directory_id`、`name`、`deleted=true|false`、`page` 和 `page_size`。页码从 1 开始，默认页大小 50、最大 100；默认只列出未删除文件。未知或重复参数被拒绝。

文件 PATCH 同时提交完整目标名称和同项目目录 ID；只改名称时也要回传当前目录。软删除立即从默认列表和普通下载入口隐藏；还原若遇到活动同名文件返回 `409 name_conflict`。移动、删除和还原不改变文件/版本/内容 ID。

远程 AI 策略只有两种：

- `internal_only`：`approved_version_ids` 必须为空，并清除所有版本批准；
- `remote_ai_allowed`：把批准集合精确设置为请求中属于该文件的去重版本 ID，新版本不会继承旧批准。

M1 只保存这个同意状态，不向远程模型发送内容。

### 上传任务

| 方法与路径 | 权限 | 请求 | 成功 |
| --- | --- | --- | --- |
| `POST /api/v1/projects/{project_id}/uploads` | admin/editor | 创建任务 JSON | `201 UploadTaskDetail` |
| `GET /api/v1/projects/{project_id}/uploads` | owner member | `state`、`page`、`page_size` | `200 Page<UploadTask>` |
| `GET /api/v1/projects/{project_id}/uploads/{task_id}` | owner member | 无正文 | `200 UploadTaskDetail` |
| `PUT /api/v1/projects/{project_id}/uploads/{task_id}/parts/{part_number}` | owner admin/editor | 原始分片 | `200 PartResult` |
| `POST /api/v1/projects/{project_id}/uploads/{task_id}/complete` | owner admin/editor | 空正文 | `200` 固定结果 ID |
| `POST /api/v1/projects/{project_id}/uploads/{task_id}/cancel` | owner admin/editor | 空正文 | `204` |

创建新文件：

```json
{
  "mode": "create_file",
  "directory_id": "<directory_id>",
  "name": "notes.txt",
  "size": 12345,
  "sha256": "<whole-file SHA-256>",
  "media_type": "text/plain"
}
```

创建新版本：

```json
{
  "mode": "create_version",
  "file_id": "<file_id>",
  "observed_current_version_id": "<version_id>",
  "size": 12345,
  "sha256": "<whole-file SHA-256>",
  "media_type": "text/plain"
}
```

任务详情包含 `task_id`、`state`、`mode`、目标、总大小/摘要/媒体类型、`chunk_size`、`part_count`、`received_bytes`、完成结果 ID、失败原因和 `confirmed_parts`。状态筛选值为 `uploading`、`assembling`、`publishing`、`completed`、`failed`、`cancelled` 或 `interrupted`。

分片编号从 `0` 开始。PUT 必须设置：

- `Content-Type: application/octet-stream`；
- 精确的 `Content-Length`；
- `X-Chunk-SHA256: <该分片的64位小写十六进制摘要>`。

普通分片大小必须等于任务返回的 `chunk_size`，最后一片可较小。0 字节文件有 0 个分片，直接完成。相同编号、大小和摘要的重放返回 `reused:true`；相同编号的不同内容返回 `409 chunk_conflict`。完成操作是幂等的，重复调用返回相同 `file_id`、`version_id` 和 `processing_job_id`，并以 `reused` 表示是否复用已提交结果。

## 下载与 Range

无 `Range` 时返回完整 `200`。只支持一个 `bytes` 范围，范围单位大小写不敏感：

```http
Range: bytes=0-1023
Range: bytes=1024-
Range: bytes=-1024
```

合法范围返回 `206`、`Accept-Ranges: bytes`、`Content-Range` 和精确 `Content-Length`。语法错误或不可满足范围返回 `416`；任何逗号/多范围返回 `501 range_not_supported`。PDF 使用 `Content-Disposition: inline`，其他媒体类型使用 `attachment`。`#page=N` 是浏览器 URL fragment，不发送给服务器。

## 端到端示例

以下示例假设 `BASE_URL=https://docs.example.com`，Cookie jar 位于本机受保护临时目录。示例值必须替换为真实服务返回值：

```bash
curl --fail-with-body -c cookie.jar \
  -H "Origin: $BASE_URL" -H 'Content-Type: application/json' \
  --data '{"username":"admin","password":"<password>"}' \
  "$BASE_URL/api/v1/auth/login"

curl --fail-with-body -b cookie.jar \
  -H "Origin: $BASE_URL" -H 'Content-Type: application/json' \
  --data '{"name":"Example"}' \
  "$BASE_URL/api/v1/projects"
```

创建上传任务后，按服务返回的 `chunk_size` 在客户端切片并分别计算摘要：

```bash
curl --fail-with-body -b cookie.jar \
  -H "Origin: $BASE_URL" \
  -H 'Content-Type: application/octet-stream' \
  -H 'X-Chunk-SHA256: <part-sha256>' \
  --data-binary @part-00000 \
  "$BASE_URL/api/v1/projects/<project_id>/uploads/<task_id>/parts/0"

curl --fail-with-body -b cookie.jar -H "Origin: $BASE_URL" \
  --data '' \
  "$BASE_URL/api/v1/projects/<project_id>/uploads/<task_id>/complete"

curl --fail-with-body -b cookie.jar \
  -H 'Range: bytes=0-1023' \
  "$BASE_URL/api/v1/projects/<project_id>/files/<file_id>/content" \
  --output first-1024.bin
```

不要把 Cookie jar、密码或真实文档加入仓库。

## 限制

| 项目 | 默认/上限 |
| --- | --- |
| 协议 | HTTP/1.1；不支持 Transfer-Encoding、请求流水线 |
| 请求行 | 最大 8192 bytes |
| 请求头 | 合计最大 32768 bytes |
| POST/PUT/PATCH | 必须有 `Content-Length` |
| JSON 正文 | 默认 1 MiB；配置范围 1 byte–64 MiB |
| 文件 | 默认 1 GiB；配置最大 1 TiB |
| 分片 | 默认 8 MiB，且不大于文件上限 |
| 分片数 | 最多 1,000,000，编号从 0 开始 |
| 文件/目录/项目名 | 去除首尾空白后 1–255 bytes，不含 `/` 或 NUL |
| 媒体类型 | 最多 255 bytes，标准 token/token 形式 |
| 分页 | page 默认 1；page_size 默认 50、最大 100 |
| 会话 | 默认 43,200 秒；配置范围 60–31,536,000 秒 |
| Range | 只支持一个 `bytes` 范围 |

## 主要错误码

| HTTP | code | 含义/处理 |
| --- | --- | --- |
| 400 | `invalid_request`, `invalid_part_number`, `body_not_allowed`, `host_required` | 修正参数、正文、Content-Length 或请求头 |
| 401 | `authentication_required`, `invalid_credentials` | 重新登录，不复用失效 Cookie |
| 403 | `forbidden`, `origin_mismatch` | 当前角色不允许，或 Origin/Host 不匹配 |
| 404 | `resource_not_found`, `route_not_found` | 对象不存在或不可向调用者泄露 |
| 405 | `method_not_allowed` | 使用路由规定的方法 |
| 409 | `name_conflict`, `chunk_conflict`, `version_conflict`, `upload_state_conflict`, `upload_parts_incomplete`, `upload_interrupted` | 刷新权威状态后决定重试或让用户处理冲突 |
| 411 | `length_required` | 为 POST/PUT/PATCH 提供 Content-Length |
| 413 | `body_too_large`, `file_too_large`, `too_many_parts` | 减小正文/文件或调整受控配置 |
| 414 | `request_line_too_large` | 缩短 URL |
| 416 | `range_not_satisfiable` | 重新读取版本大小并构造范围 |
| 422 | `chunk_digest_mismatch`, `whole_file_mismatch` | 重新计算摘要并重新选择/上传正确文件 |
| 431 | `headers_too_large` | 减少请求头 |
| 501 | `range_not_supported` | 改为单段 Range |
| 503 | `database_unavailable`, `database_busy`, `storage_unavailable`, `content_unavailable`, `upload_completion_in_progress` | 仅当 `retryable:true` 时退避重试 |

错误响应中的 `request_id` 用于关联受控服务日志；不要把密码、Cookie、上传正文或内部绝对路径写入问题单。
