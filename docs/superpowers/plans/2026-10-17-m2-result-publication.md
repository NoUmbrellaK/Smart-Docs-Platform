# M2 Result Publication Implementation Plan

> **For agentic workers:** Use executing-plans in the existing M2 worktree. User approved architecture A and autonomous routine decisions; do not request repeated approval.

**Goal:** Persist validated version-bound parser results, atomically publish local keyword readiness, and expose authenticated exact-version source reads.

**Architecture:** Extend existing ProcessingService; MySQL owns immutable result/chunk rows and ngram FULLTEXT. Validate bounded parser JSON before the transaction; recheck file/current version/attempt under existing locks, then insert all chunks and update states in one transaction. No worker transport or model requests in this increment.

**Tech Stack:** Existing C++14, nlohmann JSON, MySQL 8; schema 4. No additional dependency.

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md` sections 2–4; requirements R-01/R-03/F-04 and T-06/T-07/T-08.

## Global Constraints

- Source input at most 8 MiB; 2,000,000 Unicode code points, 2,000 chunks, 1,200 code points/chunk, 300 PDF pages.
- Identity matches Python parser revisions and SHA-256 formula; page-local contiguous offsets/lines; explicit PDF empty-page coverage. Document text is never instructions or executable HTML.
- Results immutable per version. Same attempt + identical result replay succeeds; different result conflicts. Cancelled/retried/interrupted/superseded/deleted/unavailable publication rejected without partial writes.
- Parse, keyword and vector states separate. Local FULLTEXT publication is not vector or hybrid readiness. AI policy changes update vector applicability, never send content.
- Public source reads require reader and an exact project/file/version/chunk match; history does not jump to current version, deleted/unavailable content is denied. These are local user reads, NOT Agent/remote authorization.
- Same server only; one build/test unit, -j1, MemoryHigh=1200M/MemoryMax=1600M/MemorySwapMax=0, CPUQuota=100%, Nice=10, TasksMax=256, /dev/vda 5 MB/s and 200 IOPS per direction, 900s timeout. Stop own unit if available memory <512 MiB. Original MySQL unchanged.

## Task 1: Validated publication and source reads

**Files:** `code/processing/document_result.{h,cpp}` validator; extend `processing_service.{h,cpp}` and `processing_routes.cpp`; new `db/migrations/004_document_results.sql`; update schema expectations/test cleanup and AI applicability in `code/file/file_repository.cpp`; tests in `test/integration/processing_jobs_test.cpp` and `test/unit/document_result_test.cpp`; API/README evidence.

**Interfaces:**

```cpp
ValidatedDocument ValidateDocumentResult(const nlohmann::json&);
void ProcessingService::Publish(const std::string& job_id,
    const std::string& attempt_token, const nlohmann::json& result);
nlohmann::json ProcessingService::ReadDocument(const SessionContext&,
    const std::string& project, const std::string& file, const std::string& version,
    const std::string& chunk_id = "");
```

Result envelope: file_id, version_id, source_sha256, parser_revision, page_count (0 for text), pages_without_text, chunks (existing Python dataclass fields; page only for PDF). Validate exact keys, integers (not floats/bools), UTF-8, controls, count/line/coverage/identity. Result digest hashes canonical JSON, excluding transport attempt. The trusted worker is responsible for real extraction; validation cannot independently prove arbitrary text came from a PDF.

- [x] RED: add GET exact-version document state and missing chunk tests through existing Application; verify compiled old implementation fails with route_not_found.
- [x] Implement validator and schema 4; add runnable boundary and Python-compatible identity tests. Backfill existing parse states without declaring old completed jobs indexed; legacy completed-without-results becomes interrupted for manual retry.
- [x] Publish under file→queue→job locks; match immutable source SHA/size, insert immutable result/chunks and FULLTEXT, mark parse/keyword completed, vector pending/not_applicable. Use existing BeforeDatabaseCommit fault point to prove rollback. Identical replay does not replace chunks.
- [x] GET `/api/v1/projects/{project_id}/files/{file_id}/versions/{version_id}/document` returns independent states, counts and PDF coverage; GET same prefix `/chunks/{chunk_id}` returns one bounded source chunk. No search or pagination claim.
- [x] Tests: exact identity/coverage/malformed payload, publication replay/conflict, stale/cancel/restart tokens, deleted/superseded/unavailable publish, fault rollback, real MATCH lookup, exact historical reference, reader/cross-project/revoked access and AI applicability.
- [x] GREEN: single capped full C++ run, schema 2→4 upgrade and replay, Python parser regression. Independent static review; document scope and measured results; normal commit/push M2 branch, not main.

Representative checks:

```cpp
CHECK_THROWS_CODE(jobs.Publish(old.id, old_token, result), "attempt_conflict");
CHECK(db.ScalarInt("SELECT COUNT(*) FROM document_chunks") == 0);
jobs.Publish(job.id, claim.attempt_token, result);
CHECK(jobs.ReadDocument(session, project, file, version).at("keyword_state") == "completed");
```

No automatic worker, public submission endpoint, vector generation, search UI, or M2 completion claim in this increment. Next step connects Python worker to these real publication/reading boundaries.

## 2026-10-17 验证证据

基线提交 `2c27c44`，分支 `codex/m2-document-processing`。当前 ECS 串行验证，没有远程模型调用，也没有升级原有业务库。

| 检查 | 实际结果 |
| --- | --- |
| RED 来源接口（旧实现成功编译） | 0 passed / 2 failed / 0 skipped；缺少 document/chunk 路由；不将此说成所有新增函数都跑过 RED |
| 首轮完整 C++ 回归 | 112 passed / 0 failed / 0 skipped |
| 审阅后结果校验定向复验 | 7 passed / 0 failed / 0 skipped |
| 审阅后任务/来源定向复验 | 19 passed / 0 failed / 0 skipped |
| schema 升级/重放 | 首轮 102 文件夹具通过；补强为 103 文件，100 pending、1 queue_full、1重启中断、1旧 completed 转 results_unavailable；103 个文件当前版本保留，parse_state 同步，keyword 全 pending，无伪造结果/片段 |
| TXT/Markdown/PDF Python 回归 | 28/28，3.616 秒；包括真实 PDF 子进程资源/超时测试 |
| Shell 与补丁检查 | bash -n、git diff --check 通过 |

两轮 C++ 结果不能简单相加：第二轮复测了重叠用例，并补充测试，不是又一次完整套件。两轮生产代码完全相同；第二轮仅修改测试和迁移夹具，并消除了测试中的范围循环复制编译警告。最终 code/test/db/scripts 与执行副本逐目录 diff 一致，Makefile cmp 一致。

执行副本 `/tmp/smartdocs-safe.h4SzvX/repo` 复用了增量产物，不是本增量的干净 Git checkout。每轮运行时保持副本不变，退出后才同步。原始日志在当前机器 `/tmp/smartdocs-safe.h4SzvX/m2-results-{red,green,review}.log`，属于临时证据，可能被系统清理。

首轮受限单元 `smartdocs-m2-results-green`：17:36:30–17:45:30 CST，journal 记录 Deactivated successfully，CPU 时间 1min 1.147s。命令：

```bash
make -j1 bin/smartdocs_tests && scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests
```

补强单元 `smartdocs-m2-results-review`：17:46:05–17:50:40 CST，同样成功退出，CPU 时间 26.794s：

```bash
make -j1 bin/smartdocs_tests &&
stdbuf -oL ./bin/smartdocs_tests --filter=document_result_ &&
scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests --filter=processing_
```

两单元均为 nobody:nogroup，PrivateTmp，绑定测试副本路径，使用本计划资源限额；原 MySQL 未改动。抽样可用内存约 1.7 GiB，不能当成全程最低值。之后串行运行 Python：

```bash
ulimit -v 524288
ulimit -t 60
PYTHONPATH=python timeout 90s /tmp/smartdocs-m2-pdf.FLxk0l/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

独立只读审阅没有发现阻塞性代码问题；已补上其提出的多片段/多页、四字节 Unicode、2,000 块与 2,000,000 字符边界、重试旧 token、不可用来源、file/version 不匹配及旧 completed 迁移测试。PDF 黄金 ID 由真实 Python parse_pdf 对仓库样例生成，未使用 C++ 被测 ID 算法生成预期值。

覆盖边界：C++ 集成测试使用构造的数据库元数据，不等于真实上传→Python worker→索引端到端验收；仅中文词“部署”的 MATCH 查验，不等于完整搜索质量测试。事务回滚覆盖提交前异常，不是新做的进程 kill/断电恢复测试。未重跑浏览器与大文件验收；无向量能力或性能成绩声明。

最终被测源 SHA-256：

```text
b620fc46f492ad573e199087ca4165d94020118e24b049fbfa9e4d8b39e2e149  code/processing/document_result.cpp
008fcb2d31bed098ed411d648ddc00e91750e5a4bed0983b276efc3caa1e6a43  code/processing/processing_service.cpp
ba19a414d8ba91ff7101856e560a19ed4151837bd0929cd3d79ab800ccd58e66  db/migrations/004_document_results.sql
6f0c697ae25f1e74b98c63eb432e57ac8a085ff5d45f279f1eca7efaa930646e  test/unit/document_result_test.cpp
5615790cbc0ea165b3f2086bdac03af5bf5b358b28b6cc6d91b13b8e7e765188  test/integration/processing_jobs_test.cpp
d3b2d25a44d6578c6a39f143ed971e902477951091fc5683def4f2fd5e023b52  test/e2e/m2_migration_test.sh
```
