# M2 Local Worker Implementation Plan

> **For agentic workers:** Use executing-plans in the existing M2 worktree. Architecture A and routine implementation are already approved; no repeated approval menu.

**Goal:** Run the real local upload → claim → bounded source read → Python TXT/MD/PDF parser → transactional C++ publication workflow.

**Architecture:** Optional authenticated internal HTTP routes on the existing loopback C++ listener, with an independent 64-hex worker secret. Python stdlib HTTP client talks only to 127.0.0.1, never follows redirects or proxies. One local worker lock; startup marks abandoned running tasks interrupted for manual retry, not automatic resubmission. No schema change or new queue/framework.

**Tech Stack:** Existing C++14/OpenSSL/MySQL and Python 3.10+/pypdf. Reuse ProcessingService, FileStore, AuthorizedVersion, JSON body limit and M1 real-socket test helpers.

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md`, requirements R-01/R-03/section 10.

## Global Constraints

- Disabled without SMARTDOCS_WORKER_TOKEN; when enabled, C++ listen_address must be exactly 127.0.0.1. Reject browser Origin/Cookie even with a bearer. Constant-time secret comparison; tokens never in URLs, logs or responses to user routes.
- Worker is a trusted local service allowed to parse internal-only files locally, not an Agent identity or permission to call a remote model. No DeepSeek configuration consumed.
- Pending≤100/running≤1 remains authoritative. Source access requires matching active attempt, current nondeleted available version, size≤8 MiB and controlled content ID. Never accept disk paths or arbitrary URLs from a task.
- Publication JSON≤16 MiB, fail JSON≤1 KiB; ordinary user JSON limit unchanged. Real parser limits/UTF-8/hash checks preserved. Single worker and bounded requests; no new thread per task.
- Recover only after acquiring the shared local worker flock. C++ restart also interrupts running work. Cancellation prevents new stages where checked and always prevents publication; an already running parser may finish within its bound, not instantaneous kill.
- Claim is not automatically retried after uncertain delivery. Publish/fail may retry exactly once on transport/5xx using the same IDs/token/body. Never mark an ambiguous committed publish failed or create a new task.
- Same-server serial heavy verification only: -j1, 1200M high/1600M max/0 swap, CPUQuota100%, Nice10, Tasks256, /dev/vda read/write 5 MB/s and 200 IOPS, 900s/unit; stop own unit at available memory<512 MiB. Original MySQL unchanged.

## Task 1: Internal transport and real worker

**Files:** config.{h,cpp}/application.cpp, processing_service.{h,cpp}, new worker_routes.{h,cpp}, python/smartdocs/worker.py; config examples and worker deployment guide. Tests: config_test.cpp, processing_jobs_test.cpp, test/python/test_worker.py, test/e2e/m2_worker_test.py.

**Interfaces:**

```cpp
void AppConfig::ValidateWorkerSettings() const;
// ProcessingClaim adds source_sha256, source_size, kind (txt/md/pdf/empty unsupported).
AuthorizedVersion ProcessingService::OpenInput(const std::string& job,
    const std::string& attempt, FileStore& store);
```

Internal prefix `/internal/v1/processing`: POST `/claim` and `/recover` without body; GET `/{job_id}/content`; POST `/{job_id}/result` (existing result envelope) and `/{job_id}/fail` (`{"error_code":"..."}`). All require bearer; per-job operations require `X-SmartDocs-Attempt`. Claim data is null or `{id,file_id,version_id,attempt_token,source_sha256,source_size,kind}`. Content returns the exact immutable bytes, not a path. Result/fail return success only after committed service calls.

Python `Client(port, token)`, `run_once(client)`, `worker_lock(path)` and module CLI `python -m smartdocs.worker [--once]`. Config: SMARTDOCS_WORKER_TOKEN, SMARTDOCS_WORKER_PORT (1316 default), SMARTDOCS_WORKER_LOCK_PATH (absolute private runtime path). CLI startup acquires lock then recover; loop polls every 5 seconds, logs only safe status codes, stops on transport/config failure. `--once` performs one poll, not a guarantee the whole queue is empty.

- [x] RED: config worker tests reject malformed secret or non-loopback listener in old implementation; Python worker entrypoint missing fails explicitly.
- [x] Implement optional auth/route registration, bounded source opening and claim metadata. Test missing/wrong bearer, browser headers, disabled routes, wrong/old attempt, size/format rejection and result body limits without loosening user limits.
- [x] Implement stdlib worker, local lock and exact replay policy. Test real parser output using a recording local test service/client, malformed claim, hash mismatch, unsupported format, truncated acknowledgement, no redirect/external host and lock exclusion.
- [x] Real sockets: upload TXT, MD and PDF with existing M1 helper; execute real worker; check C++ states/source chunks/current-vs-history; unsupported/damaged input failure; cancellation/restart/manual retry; authentication denial and no plaintext secrets in logs. No mocked parser for this acceptance.
- [x] Run bounded build, full C++ and Python regressions and isolated real-socket test. Independent static review, README/API/deployment instructions and true evidence. Handoff on the existing M2 branch; leave main untouched.

Representative checks:

```cpp
environment.Set("SMARTDOCS_WORKER_TOKEN", std::string(64, 'a'));
environment.Set("SMARTDOCS_LISTEN_ADDRESS", "0.0.0.0");
CHECK_THROWS_CODE(AppConfig::LoadFromEnvironment(), "config_invalid");
```

```python
assert run_once(client) == "completed"
assert document_status["parse_state"] == "completed"
assert document_status["vector_state"] == "not_applicable"
```

No search UI, mixed retrieval, remote calls, multi-worker lease/heartbeat, or M2 completion claim in this increment.

## 2026-10-17 验证记录

基线 `a0c3af7`，工作树 `.worktrees/m1-file-business`，分支 `codex/m2-document-processing`。生产代码及测试集合指纹（包含未跟踪的新文件，不含文档）：

```bash
git ls-files -z --cached --others --exclude-standard -- code python test Makefile \
  | sort -z | xargs -0 sha256sum | sha256sum
# 2a3b864f17fdc8e1d56b8f60543468160cb5b730b1b04fce508490f3249ee193
```

上述列表逐文件 cmp 与执行副本 `/tmp/smartdocs-safe.h4SzvX/repo` 一致。测试日志在同一临时根；这些临时路径不是生产部署位置，也不承诺永久保留。

- RED：worker 模块尚不存在时 Python 入口测试失败；配置测试首次有测试自身的 C++ 初始化列表类型错误，修正后再跑 `smartdocs-m2-worker-red-fixed`，2 项均因旧实现未拒绝配置而失败。编译错误不计作有效 RED。
- 独立静态审阅发现 Python `HTTPResponse.read(limit + 1)` 对提前 EOF 不抛错。真实回环 HTTP 新增 3 项测试，修复前 3 项失败（其中 publish/fail 子场景共计 4 条失败断言），增加 `response.length > 0` 检查并转为 `IncompleteRead` 后 3 项通过；发布/失败确认最多原样重放一次，源文件/claim 不重放且不得误判为损坏。
- 构建：`smartdocs-m2-worker-build`，`make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin`，systemd Result=success；日志 `m2-worker-build.log`。
- Python：`smartdocs-m2-worker-e2e` 中 `PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v`，**39/39**（文本 13、PDF 15、worker 11），0 失败，3.836 秒。
- 同一单元串行执行 `PYTHONPATH=python scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m2_worker_test.py`，真实 HTTP **7/7**，0 失败，测试体 11.888 秒（不含初始化/迁移）；schema 2→4 的 103 文件迁移夹具通过。单元 Result=success、ExecMainStatus=0；日志 `m2-worker-e2e.log`。
- C++：`smartdocs-m2-worker-regression`，`scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests`，**121 passed, 0 failed, 0 skipped**。单元 Result=success、ExecMainStatus=0、ActiveState=inactive；日志 `m2-worker-regression.log`。结束后原 mysql.service 仍为 active，可用内存采样约 2.1 GiB。
- 独立静态审阅仅发现上述响应截断 P2，已通过先红后绿测试修复；未报告其他可操作安全/正确性问题。主代理复核改动与测试副本一致，`git diff --check` 通过。审阅本身未运行数据库或构建。

所有重型单元串行、nobody/nogroup、PrivateTmp，使用本计划开头的 CPU/内存/I/O/900 秒限制。MySQL 是临时目录下的独立 skip-networking 实例；未迁移/停用原 MySQL、未扩容/加 swap。现有 PDF 虚拟环境复制到测试可访问目录，无新增网络安装。没有读取 DeepSeek 配置或发起模型请求。

实际覆盖：TXT/MD（包括 octet-stream 扩展名回退）/真实两页 PDF；中文 FULLTEXT；60 万 Unicode 字符、500 块且结果超过普通 1 MiB JSON 限额；普通接口依然拒绝超限声明；损坏/空白/不支持文件失败且原文仍可下载；取消、旧 token、新版覆盖释放旧任务名额；共享锁竞争在恢复前退出；真实 C++ 停启后人工重试；worker 启动恢复遗留 running；认证与日志无 worker 凭据泄漏。

范围限制：worker 重启恢复使用真实 CLI + 预先领取的 running 任务，不声称已做解析中 SIGKILL 全排列；发布确认截断是本地 HTTP 协议夹具，数据库幂等性由 C++ 回归另测，不声称网络故障注入贯穿全部生产链路。当前测试不是最大文件/长期压力或 M1 浏览器重验，不包含搜索业务、向量能力、生产 systemd/nginx 安装和真实远程模型。M2 尚未完成。
