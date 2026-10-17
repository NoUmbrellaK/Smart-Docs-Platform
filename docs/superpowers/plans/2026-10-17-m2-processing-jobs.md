# M2 Persistent Processing Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 延续已批准方案 A，当前服务器串行受限验证；不重复申请常规实施许可。

**Goal:** 将上传时已创建的处理任务接入可观察、可取消/人工重试、有限队列及重启中断的 C++ 状态机。

**Architecture:** 复用 MySQL `processing_jobs`，增加每次领取的随机 attempt token，C++ 保持状态与权限权威。浏览器只开放状态、取消、重试；领取/失败仅为可信 C++ 内部调用，后续增量再接认证的 Python worker 通道和实际片段发布。此阶段没有“伪完成”接口。

**Tech Stack:** C++14、现有 MySQL 8 事务/连接池与 HTTP 路由；增量 SQL migration 003。

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md`

## Global Constraints

- pending 队列上限 100，running 至多 1；队列满时上传文件仍成功保存，处理任务明确 failed/queue_full。
- 状态：pending/running/completed/failed/interrupted/cancelled。当前增量不提供 completed 写入入口，完成状态必须留待真实结果原子发布。
- 重试保留 job/version ID，新领取产生新 token、attempt_count 加一；旧 token 不能写入新 attempt。取消/重复重试不重复排入队列。
- 公共接口不暴露 attempt token、存储路径或正文；reader 可查，editor/admin 可取消/重试，复用 ProjectService 的事务内角色检查与同源检查。
- 修改锁顺序：项目成员（公共操作）→ 文件 → 全局队列 guard → 任务；上传在既有文件锁之后取得 queue guard。先选择候选再加锁并重查，不能用旧快照决定队列容量。
- 当前服务器只执行一个重型测试单元，MemoryHigh=1200M、MemoryMax=1600M、MemorySwapMax=0、CPUQuota=100%、Nice=10、TasksMax=256、磁盘 /dev/vda 读写各 5 MB/s 与 200 IOPS；15 分钟超时，可用内存低于 512 MiB 则停止本次测试。原 MySQL 不变。

---

### Task 1: 持久状态、有限入队和用户接口

**Files:**
- Create: `db/migrations/003_processing_jobs.sql`。
- Create: `code/processing/processing_service.h`、`.cpp`；包含唯一入队 helper 与事务状态机，不另建重复 repository 层。
- Create: `code/processing/processing_routes.h`、`.cpp`；状态、取消、重试路由。
- Create: `test/integration/processing_jobs_test.cpp`。
- Modify: `code/upload/upload_repository.cpp`（原上传事务调用有界入队 helper）；`code/project/project_service.h`（同事务鉴权 friend）；`code/app/application.h`、`.cpp`（注册/启动恢复/schema=3）；`test/integration/mysql_schema_test.cpp`、`scripts/test-with-mysql.sh`（预期 schema=3）。
- Modify: `README.md`、新增 `docs/api/m2-processing-api.md`（真实范围、迁移和接口）。

**Interfaces:**
- `EnqueueProcessingJob(MySqlConnection&, job_id, version_id)`：调用方必须处于持有文件锁的上传事务；pending 满则插入 failed/queue_full，并同步版本处理状态。
- `ProcessingService(MySqlPool&, ProjectService&)`；`Get/Cancel/Retry(session, project_id, job_id)` 返回 `ProcessingJob`。
- `ProcessingJob`：id、file_id、version_id、project_id、state、attempt_count、error_code（不含 token）。
- `ClaimNext(ProcessingClaim*) -> bool`：内部调用，至多返回一个版本和一次性 attempt token；默认只处理当前未删除且 available 的版本，过期候选取消。
- `Fail(job_id, token, error_code)`：内部调用，仅接受既有错误码白名单；相同 attempt 同一失败可幂等返回，旧 attempt 拒绝。
- `RecoverInterrupted()`：启动期间把 running 标记 interrupted，等待人工重试；无自动重跑。
- HTTP：`GET /api/v1/projects/{project_id}/processing-jobs/{job_id}`；无正文 `POST .../{job_id}/cancel` 与 `POST .../{job_id}/retry`。

- [x] **Step 1: 写可运行的失败回归**

沿用现有 Application/AuthService 测试方式和模拟版本元数据。先在旧实现上运行 HTTP 状态、取消/重试及队列上限回归，预期 route_not_found/容量断言失败，不以编译错误代替 RED。

```cpp
auto response = app.Prepare(JobHead("GET", project.id, job.id, admin_token))->Finish();
CHECK(Json(response)["data"]["state"] == "pending");
CHECK(Json(response)["data"].count("attempt_token") == 0);
```

- [x] **Step 2: 迁移与最小状态机**

003 扩展状态检查约束、增加 attempt_token 和 singleton queue guard。迁移既有 running 为 interrupted；既有 pending 超过 100 时保留最早 100，其他置 failed/queue_full，文件内容不变。

```sql
SELECT id FROM processing_queue_guard WHERE id=1 FOR UPDATE;
SELECT id FROM processing_jobs WHERE state='pending' ORDER BY created_at,id LIMIT 100 FOR UPDATE;
```

使用当前锁定读判断容量；当前文件指针/删除状态在文件行锁内重查。重试只允许 failed/interrupted/cancelled；pending 重试幂等，running/completed 返回冲突。失败和恢复同步版本状态，不擅自标记解析/索引完成。用户取消/重试写审计记录。

- [x] **Step 3: attempt、并发与恢复回归**

```cpp
ProcessingClaim old_claim, new_claim;
CHECK(jobs.ClaimNext(&old_claim));
jobs.RecoverInterrupted();
jobs.Retry(admin_session, project.id, old_claim.job.id);
CHECK(jobs.ClaimNext(&new_claim));
CHECK(old_claim.attempt_token != new_claim.attempt_token);
CHECK_THROWS_CODE(jobs.Fail(old_claim.job.id, old_claim.attempt_token, "invalid_pdf"), "attempt_conflict");
```

验证两个并发领取只有一个成功、取消后不能回写、队列并发入队不越界、旧版本/删除/不可用候选不能运行、跨项目与 reader 写入拒绝。不得把旧状态读缓存当事务验证。

- [x] **Step 4: 受限 GREEN、审阅和文档**

复用既有隔离测试副本和编译产物，只增量 `make -j1 bin/smartdocs_tests`；随后 `scripts/test-with-mysql.sh -- ./bin/smartdocs_tests` 验证真实 MySQL 迁移、回放及全套 C++ 回归。测试与编译放在同一受限单元中，禁止启动浏览器或模型。

独立审阅事务/权限/竞态；README 明确后台 worker 与片段发布尚未接通，M2 未完成。记录真实测试命令和结果，核验远程/分支后正常推送 M2 分支，不合并 main。

## 2026-10-17 验证结果

- RED：旧实现成功编译后运行最初 4 项回归，`0 passed, 4 failed, 0 skipped`；3 项缺少路由，1 项队列超过 100。没有以编译错误代替失败测试。后续新增的内部状态机测试不计入这次 RED。
- GREEN：当前 ECS `smartdocs-m2-jobs-green.service`，17:20:15–17:27:32 CST，单元成功退出；完整 C++ 回归 `100 passed, 0 failed, 0 skipped`（新增任务测试 10 项）。独立 MySQL 升级检查也通过，102 个文件当前版本均保留，100 pending、1 queue_full、1 interrupted，版本状态同步，重复迁移输出 schema 3。
- 命令：`make -j1 bin/smartdocs_tests && scripts/test-with-mysql.sh -- ./bin/smartdocs_tests`，在 `/tmp/smartdocs-safe.h4SzvX/repo` 的既有增量构建副本执行。该副本不是本增量的干净 Git 提交；测试后用 `diff -qr` 确认其 code/test/db/scripts 全部与提交源一致，Makefile 用 cmp 比对相同。
- 资源：按上述限额以 nobody:nogroup 运行；PrivateTmp，绑定测试副本路径；本轮未并行运行重型任务或浏览器。期间抽样可用内存约 1.7 GiB（不是全程最低值）；临时 MySQL 已退出，原 MySQL 仍 active，没有执行原业务库迁移。
- 证据日志（当前机器临时文件，不保证长期保留）：`/tmp/smartdocs-safe.h4SzvX/m2-jobs-red.log`、`m2-jobs-green.log`；systemd journal 确认 `Deactivated successfully`，CPU 时间 1min 38.573s。
- 独立静态审阅没有阻塞问题。非阻塞覆盖缺口：并发测试启动两个线程但未控制事务精确交错，不能视为穷举竞态证明；队列满的新增测试验证元数据保留，未增加队列满场景的真实字节下载测试。既有上传/下载回归全部通过。
- `bash -n scripts/test-with-mysql.sh test/e2e/m2_migration_test.sh`、`git diff --check` 通过。无模型调用；未重跑 M1 全套浏览器/流式大文件验收。没有证明自动后台解析或完成发布。

关键被测源 SHA-256（提交前记录）：

```text
69c9eb7c2262f13e5f44ea23cd0f0f76baf908261156df4234a8151cf0d3418e  code/processing/processing_service.cpp
fef78a410738a0dfaccc28504b46cb25a4bbd319836605751ad5c0fd20cd24b3  code/processing/processing_routes.cpp
89934a527a0f75e00ae35eb81b9f6e7a62d39ca77e3716cfbe6916751c215aac  db/migrations/003_processing_jobs.sql
1f096b616a8fea42901aa6db68f8da977c768b23326a7079f12b9b35aad9e01a  test/integration/processing_jobs_test.cpp
a41711bf6c90a7084ac68f121195f1eb8245c5a13e04a9a4ffe4726644bf41b2  test/e2e/m2_migration_test.sh
```
