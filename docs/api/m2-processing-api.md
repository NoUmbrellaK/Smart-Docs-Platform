# M2 持久处理任务（当前增量）

当前提供任务状态、取消、人工重试、可信本地 Python worker 通道及鉴权后的固定版本来源读取。单独启用 worker 后才会消费上传任务；pending 不代表已经解析。没有搜索业务接口或模型调用。部署见 [本地 worker 手册](../deployment/m2-local-worker.md)。

## 用户接口

所有路径以 `/api/v1/projects/{project_id}/processing-jobs/{job_id}` 开始。ID 为 32 位小写十六进制。使用既有会话 Cookie 和项目权限，所有请求正文必须为空；POST 还要求同源 Origin。

| 方法及后缀 | 最低角色 | 行为 |
| --- | --- | --- |
| GET（无后缀） | reader | 查询固定版本的任务 |
| POST `/cancel` | editor | pending/running → cancelled；重复取消返回现状 |
| POST `/retry` | editor | failed/interrupted/cancelled → pending；pending 重试返回现状 |

成功返回 HTTP 200：`{request_id, data: {id, file_id, version_id, project_id, state, attempt_count, error_code}}`。无错误时 `error_code` 是空字符串。不返回内部 attempt token、正文、存储路径或原始异常文本。任务 ID 来自既有上传完成响应。

取消/重试只在实际转换时各写一条审计记录。幂等性指重复相同状态操作；跨越后续状态转换的再次操作仍重新验证。重试不创建新任务或版本；只允许文件当前、未删除且内容 available 的版本。历史任务可以查询，但不能借重试改写当前版本。

常见错误：无项目权限/跨项目/文件已删除为 `404 resource_not_found`；reader 写入为 `403 forbidden`；非同源为 `403 origin_mismatch`；非当前版本为 `409 version_conflict`；内容不可用为 `409 content_unavailable`；状态不允许为 `409 task_conflict`；重试时队列已满为 `503 queue_full`。会话错误沿用 M1。

## 队列、恢复和内部调用

- 全库 pending 最多 100、running 最多 1。上传事务入队时队列满，仍保留已完成的上传及当前版本，任务变为 `failed/queue_full`，版本 processing_state 为 failed；不会声称文件上传失败。
- 领取时 attempt_count 加一并生成新 token。取消、服务重启或重试之后，旧 token 不能回写；同一 attempt 的相同失败回报幂等。
- 内部领取会重新检查当前版本、删除及内容可用状态。旧任务可被取消，不自动处理历史版本。
- 启动时 running → `interrupted/service_restarted`，等待人工重试，不自动重跑。单机仅运行一个 C++ 服务实例；不可用第二个实例启动恢复去接管仍在工作的第一个实例。
- 内部 `Publish(job_id, attempt_token, result)` 校验后一次性写入真实结果、片段和 MySQL 本地关键词索引，再将任务标记 completed。此状态只代表本地解析/关键词发布，不代表向量或混合检索已完成。
- 任务取消保证状态和回写隔离；不发送即时 Python 子进程取消信号，已开始的有界解析可能结束，但不能发布已取消 attempt 的结果。

事务锁顺序为成员权限（用户操作）→ 文件 → 全局队列 guard → 任务。容量和 running 检查使用当前锁定读；旧版本失败只更新该版本，不改写文件的当前版本指针。

## 结果发布与来源读取

结果绑定 file/version ID、原文 SHA-256、解析器修订和稳定 chunk ID。C++ 校验精确 JSON 字段、UTF-8/控制字符、Unicode 码点数、连续偏移和行号、每页覆盖与空页声明，以及与 Python 相同的片段 ID 算法。最大 2,000 块、每块 1,200 字符、全文 2,000,000 字符、PDF 300 页，原始版本大小最多 8 MiB。超限或不完整结构整体拒绝，不截断成功。

验证后的结果在内存暂存，随后在已有文件→队列→任务锁下检查当前版本、删除、内容可用性、原文哈希和 attempt；所有结果、片段、状态在同一事务提交。相同 attempt、相同完整结果重复回报幂等；不同内容不能覆盖已发布引用。旧 attempt 返回 `409 attempt_conflict`；失效版本返回 `409 version_conflict`；不同已发布结果返回 `409 result_conflict`。校验失败不会自行结束任务，worker 明确报告安全失败码；网络故障不能伪装为解析失败。

这是可信本地解析器的发布边界，不是对任意 worker 的真实性证明：匹配的元数据/哈希不能独立证明任意提交的文字确实来自 PDF。仅使用内部认证、受控内容读取和实际 Python 提取链路；必须保护 worker 凭据及部署代码。文档文字不当作授权或指令。

## 可信本地 worker 通道

默认禁用。C++ 与 Python 使用独立的 `SMARTDOCS_WORKER_TOKEN`（64 位小写十六进制随机凭据），启用时 C++ 只允许监听 `127.0.0.1`。反向代理必须拒绝 `/internal/`；不得把本通道作为浏览器、Agent 或第三方扩展接口。无/错误 bearer 或带任意 Origin/Cookie 均返回 `401 worker_unauthorized`。

共同前缀 `/internal/v1/processing`，全部要求 `Authorization: Bearer <token>`；三个逐任务接口还要求 `X-SmartDocs-Attempt: <32 位小写十六进制 token>`。

| 方法及后缀 | 输入/限制 | 200 响应 |
| --- | --- | --- |
| POST `/claim` | 空正文 | `data: null` 或 `{id,file_id,version_id,attempt_token,source_sha256,source_size,kind}` |
| POST `/recover` | 空正文；仅持有唯一 worker 锁后调用 | `data: {interrupted_count}` |
| GET `/{job_id}/content` | 空正文，不接受 Range | 完整原始二进制，`Cache-Control: no-store` |
| POST `/{job_id}/result` | 既有结果 JSON，最多 16 MiB | `data: {state: "completed"}` |
| POST `/{job_id}/fail` | 仅 `{"error_code":"安全错误码"}`，最多 1 KiB | `data: {state: "failed"}` |

JSON 响应沿用 request_id/data 包装。领取只返回受控格式名（txt/md/pdf 或空字符串），不返回存储路径/任意 URL。已知 MIME 决定格式，仅 application/octet-stream 可回退到扩展名。

内容读取重新锁定文件/队列/任务，要求当前、未删除、available、running 且 attempt 匹配，大小不超过 8 MiB；通过 FileStore 受控 content ID 打开并核验文件大小。描述符释放给既有流式响应，Python 再检查实际大小和原始 SHA-256。取消/旧 attempt 返回 409；过大为 413，不支持为 422；不可读内容为 503。不接受用户指定的文件路径。

Python 仅连接固定 `127.0.0.1`，忽略代理环境、不跟随重定向。claim/content/recover 不自动重放；publish/fail 的传输失败（含 Content-Length 截断）或 5xx 最多重放一次，保持同一 job、attempt 和正文。两次仍失败则停止，不能据此假定事务未提交或将已完成发布改成失败。重启恢复后由用户查看状态并人工重试。启动先持有共享 flock，再 recover；每个数据库仅允许一个服务和一个 worker，不能用不同锁路径绕开。

来源 API 均为无正文 GET，最低 reader，会话及项目权限沿用 M1：

- `/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/document`：返回 data 中的固定 file/version ID、is_current、source_sha256、parser_revision、parse_state、keyword_state、vector_state、chunk_count、char_count、page_count 和 pages_without_text。尚未发布时计数为 0，不伪造就绪。
- 同一版本前缀的 `/chunks/{chunk_id}`：返回上述元数据及单个 chunk（正文、稳定 ID、页码或行号和码点区间）；只返回该精确版本的片段，不能将另一版本片段 ID 混用。

历史版本不会跳转新版；删除、跨项目或撤销成员资格后不可读；内容 unavailable 时返回 `409 content_unavailable`。本接口属于普通本地阅读，不是 Agent 外发授权入口，未来 Agent 不能绕过额外的具体版本外发检查直接转发结果。文本以 JSON 数据返回，前端必须用 textContent 等安全方式显示。

parse_state 单独描述解析阶段；keyword_state 只有同事务成功写入 FULLTEXT 后为 completed；vector_state 对内部资料为 not_applicable、批准版本为 pending，目前没有向量完成写入。变更批准或整体撤销会同步向量适用状态，新版本默认不继承批准。搜索业务、关键词边界覆盖和真实向量验证仍在后续增量。

关键词索引使用 MySQL 的 [ngram FULLTEXT](https://dev.mysql.com/doc/refman/8.0/en/fulltext-search.html)，实际分词受 ngram_token_size 和停用词配置影响；一个中文 MATCH 回归不代表完整搜索质量验收。改变这些配置必须重建索引并重新验证，不可假定配置变更会自动重写旧索引。

## schema 2/3 → 4 升级

本分支启动及 readiness 要求 `schema_version=4`。003 新增队列 guard 和 attempt token，扩展状态约束；原 running 转 interrupted，原 pending 超过 100 时保留最早 100 个，其余变为 failed/queue_full。004 增加独立解析/索引状态及不可变结果/片段表和 FULLTEXT。旧 schema 没有持久解析结果，旧 completed 状态会标记 interrupted/results_unavailable，等待人工重试，不能凭旧状态制造索引就绪。文件、版本和内容不删除。

部署前停止所有应用/worker，备份数据库与文件存储并核验恢复路径，然后使用既有受保护环境配置执行 `scripts/migrate.sh`，预期输出 `schema_version=4`。成功后再次运行不会重复迁移。不要把 M1 手册中的 schema 2 预期套到此分支。

MySQL DDL 非完整事务原子操作。若迁移中断，不要盲目反复执行或手工伪造 schema 记录；保持服务停止，检查实际表结构，从升级前备份恢复后再升级。回退至 M1 也需要恢复匹配的 schema 2 数据库，不能只回退二进制。

开发验证只使用当前服务器上的隔离临时 MySQL、禁用网络监听、受限 systemd 单元；本次没有升级原有 MySQL 业务库。`test/e2e/m2_migration_test.sh` 仅接受隔离测试 socket，覆盖 103 个旧文件的升级、队列截断、运行中断、旧完成状态失效、版本同步和迁移重放。
