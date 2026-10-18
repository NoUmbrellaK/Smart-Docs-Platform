# M2 持久处理任务（当前增量）

> 下文schema5描述M2基线；当前M3开发分支要求[schema8](m3-tool-audit.md#schema8和部署)，
> scripts/migrate.sh应输出schema_version=8。部署当前代码时须同时遵循M3迁移说明。

当前提供任务状态、取消、人工重试、可信本地 Python 解析 worker 通道、C++ 向量分批发布及鉴权后的固定版本来源读取。单独启用解析 worker 后才会消费上传任务；pending 不代表已经解析。元数据/关键词搜索保持本地执行；混合检索通过独立编码服务及同源网关接入，见[搜索 API](m2-search-api.md)。部署见[本地 worker 手册](../deployment/m2-local-worker.md)和[向量 worker 手册](../deployment/m2-vector-worker.md)。

## 用户接口

所有路径以 `/api/v1/projects/{project_id}/processing-jobs/{job_id}` 开始。ID 为 32 位小写十六进制。使用既有会话 Cookie 和项目权限，所有请求正文必须为空；POST 还要求同源 Origin。

| 方法及后缀 | 最低角色 | 行为 |
| --- | --- | --- |
| GET（无后缀） | reader | 查询固定版本的任务 |
| POST `/cancel` | editor | pending/running → cancelled；重复取消返回现状 |
| POST `/retry` | editor | failed/interrupted/cancelled → pending；pending 重试返回现状 |

成功返回 HTTP 200：`{request_id, data: {id, file_id, version_id, project_id, task_type, state, attempt_count, error_code}}`。task_type 为 parse_and_index 或 vectorize；无错误时 error_code 为空。不返回内部 attempt token、正文、存储路径或原始异常。固定版本 document 响应返回 `parse_job_id` 与 `vector_job_id`（各自尚无任务时为 null）；刷新页面后仍可发现持久任务，不依赖上传时的客户端内存。上传完成响应也包含解析任务 ID。

工作台打开文件后读取当前版本处理快照；版本列表中的 `View processing status` 可以切换到指定版本。解析、关键词和向量状态独立显示，不把上传完成当作索引完成。状态需手动 Refresh，没有后台轮询或自动重试。仅当前版本的可写用户显示 Cancel/Retry，历史版本和 reader 只读；最终权限与状态转换仍由 C++ 验证。操作回执不明确时清除旧按钮，要求刷新实际状态；选择文件/项目、退出登录或角色变化时丢弃过期响应。任务操作及远程 AI 批准变更使搜索结果失效，避免继续展示旧覆盖状态。

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

- `/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/document`：返回 data 中的固定 file/version ID、is_current、source_sha256、parser_revision、parse_state、keyword_state、vector_state、parse_job_id、vector_job_id、chunk_count、char_count、page_count 和 pages_without_text。尚未发布时计数为 0，不伪造就绪。
- 同一版本前缀的 `/chunks/{chunk_id}`：返回上述元数据及单个 chunk（正文、稳定 ID、页码或行号和码点区间）；只返回该精确版本的片段，不能将另一版本片段 ID 混用。

历史版本不会跳转新版；删除、跨项目或撤销成员资格后不可读；内容 unavailable 时返回 `409 content_unavailable`。本接口属于普通本地阅读，不是 Agent 外发授权入口，未来 Agent 不能绕过额外的具体版本外发检查直接转发结果。文本以 JSON 数据返回，前端必须用 textContent 等安全方式显示。

parse_state 单独描述解析阶段；keyword_state 只有同事务成功写入 FULLTEXT 后为 completed。vector_state 独立取 not_applicable/pending/processing/completed/failed/interrupted/cancelled；仅所有片段向量发布后为 completed，不代表搜索 API 已实现混合排名。向量取消、失败、重试和重启不重置本地 parse/keyword 完成状态。

关键词索引使用 MySQL 的 [ngram FULLTEXT](https://dev.mysql.com/doc/refman/8.0/en/fulltext-search.html)，实际分词受 ngram_token_size 和停用词配置影响；一个中文 MATCH 回归不代表完整搜索质量验收。改变这些配置必须重建索引并重新验证，不可假定配置变更会自动重写旧索引。

## 向量任务和分批发布

使用现有 processing_jobs 的 vectorize 类型。已批准当前版本解析完成时入队；
解析后才批准也会入队。共用全库 100 pending/1 running 限额；队列满保留本地
完成状态，向量任务为 failed/queue_full。内部资料和未获批新版不进入向量队列。

同一内部前缀、bearer 和 Origin/Cookie 限制：

| 方法及后缀 | 输入 | data |
| --- | --- | --- |
| POST `/vector/claim` | 空正文 | null 或 `{id,file_id,version_id,attempt_token}` |
| POST `/vector/recover` | 空正文；仅唯一向量 worker 锁持有者 | `{interrupted_count}`，只中断向量任务 |
| GET `/vector/{job_id}/chunks` | attempt 头；无正文/query/Range | `{batch_start,chunk_count,embedding_revision,chunks:[{chunk_id,text}]}` |
| POST `/vector/{job_id}/result` | attempt 头；`{batch_start,result}` | `{state:"running"或"completed"}` |

result 为[有界编码器](../evidence/m2/bounded-embeddings/README.md)原样输出。
每批恰为当前游标开始的至多 8 个权威片段，不允许任意选片段、跳批或少交。
取源与发布均重新检查具体版本批准、当前版本、删除、available、解析完成及
attempt。原 claim/recover 只处理解析；C++ 启动恢复两类任务。原 `/{job_id}/fail`
也接受编码器固定安全错误码；用户 status/cancel/retry 仍使用项目角色规则，
向量重试另外要求当前版本批准和解析完成，否则返回 vector_not_allowed。

请求正文上限 2 MiB + 4 KiB 包装，result 自身最多 2 MiB；每片段最多 64 窗、
每批 128 窗、每版本 4096 窗。C++ 校验固定修订、片段 ID/顺序/文本 SHA-256、
完整连续码点区间、384 维有限单位向量；token 预算由固定 Python 编码器实施。
这些校验不证明语义质量，协议测试中的单位向量夹具不代表真实模型结果。

向量、批指纹和游标同事务提交；最后一批才令 vector_state=completed。暂存行
不可作为就绪索引使用。相同 attempt/批起点/结果重放返回当前任务状态，不重复
插入；异内容为 result_conflict，跳批为 batch_conflict。超过版本预算返回
413 embedding_window_limit，不提交该批；worker 应报告失败，不无限重试。

每次显式修改批准清单都会取消该文件已有向量 attempt 并清空 token，再为合规
当前版本排队。即使撤销后重新批准，旧 token 也不能读下一批或发布；此保守策略
也使相同批准清单重新编码。再次领取清理前次暂存并从 0 开始，取消/中断后的人工
重试同样产生新 token。不支持从任意编码步骤续跑，也不即时终止已启动子进程。
旧向量可暂留磁盘，但状态、批准及 attempt 禁止复用；未来检索仍须重新检查
C++ 权威当前版本与成员权限。Python 向量调度与混合检索已接入，但需单独部署启用；不代表生产常驻服务或 M2 阶段验收已完成。

## schema 2/3/4 → 5 升级

本分支启动及 readiness 要求 `schema_version=5`。003 新增队列 guard 和 attempt token，扩展状态约束；原 running 转 interrupted，原 pending 超过 100 时保留最早 100 个，其余变为 failed/queue_full。004 增加独立解析/索引状态及不可变结果/片段表和 FULLTEXT。旧 schema 没有持久解析结果，旧 completed 状态会标记 interrupted/results_unavailable，等待人工重试。005 增加向量任务类型、进度、批指纹与窗口表，为已发布且合规当前版本排队；超出共享队列容量为 failed/queue_full，不伪造完成。文件、版本和原始内容不删除。

部署前停止所有应用/worker，备份数据库与文件存储并核验恢复路径，然后使用既有受保护环境配置执行 `scripts/migrate.sh`，预期输出 `schema_version=5`。成功后再次运行不会重复迁移。不要把 M1 手册中的 schema 2 预期套到此分支。

MySQL DDL 非完整事务原子操作。若迁移中断，不要盲目反复执行或手工伪造 schema 记录；保持服务停止，检查实际表结构，从升级前备份恢复后再升级。回退至 M1 也需要恢复匹配的 schema 2 数据库，不能只回退二进制。

开发验证只使用当前服务器上的隔离临时 MySQL、禁用网络监听、受限 systemd 单元；本次没有升级原有 MySQL 业务库。`test/e2e/m2_migration_test.sh` 仅接受隔离测试 socket，覆盖 103 个旧文件的升级、队列截断、运行中断、旧完成状态失效、版本同步和迁移重放。
