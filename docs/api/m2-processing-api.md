# M2 持久处理任务（当前增量）

当前提供任务状态、取消、人工重试及 C++ 内部领取/失败/启动恢复。Python 解析器尚未接通后台任务，片段持久化、索引发布、检索和完成回调尚未实现；上传后 pending 不代表已经解析。没有公开 worker 接口，也不读取模型密钥。

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
- `completed` 保留为终态，但当前没有写入入口；必须等真实解析/索引结果的原子发布实现后启用。
- 任务取消目前保证状态和回写隔离，不代表已经实现对 Python 解析子进程的取消信号。worker 通道将在下一增量实现。

事务锁顺序为成员权限（用户操作）→ 文件 → 全局队列 guard → 任务。容量和 running 检查使用当前锁定读；旧版本失败只更新该版本，不改写文件的当前版本指针。

## schema 2 → 3 升级

本分支启动及 readiness 要求 `schema_version=3`，使用 `db/migrations/003_processing_jobs.sql`。迁移新增队列 guard 和 attempt token，扩展任务/版本状态约束；原 running 转 interrupted，原 pending 超过 100 时保留最早 100 个，其余变为 failed/queue_full。文件、版本和内容不删除。

部署前停止所有应用/worker，备份数据库与文件存储并核验恢复路径，然后使用既有受保护环境配置执行 `scripts/migrate.sh`，预期输出 `schema_version=3`。成功后再次运行不会重复迁移。不要把 M1 手册中的 schema 2 预期套到此分支。

MySQL DDL 非完整事务原子操作。若迁移中断，不要盲目反复执行或手工伪造 schema 记录；保持服务停止，检查实际表结构，从升级前备份恢复后再升级。回退至 M1 也需要恢复匹配的 schema 2 数据库，不能只回退二进制。

开发验证只使用当前服务器上的隔离临时 MySQL、禁用网络监听、受限 systemd 单元；本次没有升级原有 MySQL 业务库。`test/e2e/m2_migration_test.sh` 仅接受隔离测试 socket，覆盖 102 个旧文件的升级、队列截断、运行中断、版本同步和迁移重放。
