# M2 Local Search Implementation Plan

> Execute inline with executing-plans in the existing isolated M2 worktree. Architecture A and routine implementation are approved; no additional approval menu. Reuse ponytail/TDD and serial resource-limited verification.

**Goal:** Authenticated project-scoped metadata and keyword search with truthful coverage and fixed-version sources.

**Architecture:** Add one C++ ProcessingService method in a separate search translation unit and one existing-session POST route. Reuse schema 4/MySQL ngram FULLTEXT, version/source contracts and project membership locking. No external service, model, cache, schema change or Agent endpoint.

**Tech Stack:** C++14, existing nlohmann JSON/MySQL prepared statements, existing integration/real HTTP tests.

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md` section 4; requirements R-02/R-03, T-01/T-06/T-08/T-09. This is the backend portion; search UI, genuine vector/hybrid retrieval and Agent authorization remain separate increments, not claimed complete.

## Constraints and interface

- POST `/api/v1/projects/{project_id}/search`, existing Cookie + same-origin, reader minimum. JSON body ≤4096 bytes, no URL query parameters. Strict fields: `mode` (metadata/keyword), `query` (UTF-8 ≤256 bytes), optional `directory_id`, `media_type`, `page` (1–1000), `page_size` (1–20, default 10). Empty query only for metadata. No client identity or remote-AI bypass flag.
- Metadata searches literal name substrings (`LOCATE`, not wildcard LIKE), including unparsed/unavailable current versions; empty query lists scope. Media type normalized by existing upload validator; directory scope is exact, not recursive.
- Keyword natural-language MATCH uses only available, completed parse/keyword results joined to current nondeleted files. Natural-language terms are not Boolean SQL operators. Order by relevance then chunk ID; no raw score exposed. Metadata order file ID. Prepared parameters only.
- Candidates/coverage obtained in one authorized transaction. Hold membership SHARE lock; collect only ≤21 candidate IDs, lock candidate files in sorted ID order, recheck current/deleted/directory/name/type/version availability and indexed state with current locking reads before returning text. Immutable chunks read only after this check. Do not trust candidates from the initial snapshot. No unbounded candidate text buffering.
- Responses: `mode`, `retrieval`, `items`, `page`, `page_size`, `has_more`, `next_page`, `truncated`, `coverage` and `warnings`. Item contains current file/directory/name, exact version, MIME, parse/keyword/vector/availability states; keyword additionally full ≤1200-character chunk with existing location/hash/revision and PDF coverage. No HTML highlighting or raw storage paths.
- Coverage counts current files in project/directory/MIME scope and keyword-ready subset, not number of matches; explicitly snapshot counts, not global catalog or comprehensive recall. Warnings cover unindexed content, ngram/stopword limits, unavailable vector search and live pagination. Revalidated removals flag `scope_changed`; pages may shift between requests. At page 1000 with more matches return truncated and no next_page. No false hybrid/complete claim for zero hits, short terms or punctuation.
- Local normal-user search may include internal-only files. It is NOT an Agent endpoint; future Agent path must apply exact-version remote-AI approval before metadata/content leaves C++.
- Use only current ECS; one heavy unit at a time. nobody/nogroup, MemoryHigh=1200M, MemoryMax=1600M, MemorySwapMax=0, CPUQuota=100%, Nice=10, TasksMax=256, RuntimeMaxSec=900, /dev/vda read/write 5M and 200 IOPS; stop own unit if sampled available memory <512 MiB. Temporary skip-networking MySQL only.

## Task 1: local search backend, tests and contract

Files: add `code/processing/document_search.cpp`; modify `processing_service.h`, `processing_routes.cpp`; extend `test/integration/processing_jobs_test.cpp` fixtures/tests and `test/e2e/m2_worker_test.py`; README/API/evidence updates. Existing fault observer may gain a candidate-read boundary for deterministic stale-candidate checks; no test-only service methods.

Interface:

```cpp
nlohmann::json ProcessingService::Search(const SessionContext& session,
    const std::string& project_id, const nlohmann::json& request);
```

- [x] Add route-level regression tests; observed missing-route failures before implementation. Example:

```cpp
auto response = Search(app, fixture, {{"mode","metadata"},{"query",""}});
CHECK(response.at("items").size() == 1);
CHECK(response.at("items")[0].at("parse_state") == "pending");
```

- [x] Implement strict validation/metadata/keyword query and current-state recheck. Verify zero/negative/bool/oversize/unknown params, same-origin/session/project permissions, unparsed metadata, Chinese/English/short/punctuation, literal wildcard-like input, stable pagination, MIME/directory, deleted/unavailable/superseded/restored files and source references.
- [x] Exercise a candidate discovery → concurrent version/delete/move/rename change → response boundary with existing fault observer infrastructure; returned text must follow current authoritative state, not the initial repeatable-read snapshot.
- [x] Extend true upload → worker → search → exact source HTTP tests, rerun full bounded C++ and Python/E2E regressions. Inspect independent review; fix actionable findings with tests.
- [x] Document actual results, source fingerprint, limitations and API. Handoff for normal commit/push on the existing M2 branch; do not merge main or claim M2 complete.

Representative commands, only within a capped unit:

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests --filter=processing_search
scripts/test-with-mysql.sh -- ./bin/smartdocs_tests
PYTHONPATH=python scripts/test-with-mysql.sh -- /path/to/venv/bin/python -B test/e2e/m2_worker_test.py
```

## 2026-10-17 实施与验证记录

基线 `61572dda54d21e7968b99f614ad65701a27a74e7`。继续使用 `.worktrees/m1-file-business` / `codex/m2-document-processing`，不切换或修改主工作树。普通本地阅读搜索，不实现 Agent 外发或真实向量，不调用 DeepSeek。

- RED：`smartdocs-m2-search-red` 在旧实现上先编译运行前三个新入口测试，**0 passed, 3 failed, 0 skipped**；均为 route was not found，单元正常收尾并以测试失败码 1 结束。不是编译错误。
- 定向 GREEN：`smartdocs-m2-search-focused`，`make -j1 bin/smartdocs_tests && scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests --filter=processing_search`，**6 passed, 0 failed, 0 skipped**，Result=success / ExecMainStatus=0。覆盖真实 MySQL MATCH、权限/目录/MIME/名称、分页、当前版本及故障观察点后的新提交状态。
- 静态独立审阅未发现需阻断的生产代码问题。两个 P3 建议已落实：外项目资料改用显式独有文件名；补负数/越界/零/浮点分页参数，并在已有 500 块真实 HTTP 夹具上检查两个不同的 20 块页面。另补默认英文停用词 `or`。这些是后续复验的测试增强，不改生产逻辑。
- 构建及 HTTP 回归单元 `smartdocs-m2-search-http` 先构建 tests/server/admin，再串行 Python 和真实 HTTP；Python **39/39**，3.666 秒；扩展后的真实 HTTP **7/7**，测试体 11.993 秒（不含编译/数据库初始化/迁移），0 失败；Result=success / ExecMainStatus=0。三种格式搜索均命中真实 worker 发布的片段并可按固定 ID 读取；500 块文件的两页共 40 个不同片段，无重复；损坏/不支持文件的 metadata 可发现且 parse_state=failed。
- `smartdocs-m2-search-regression`，`make -j1 bin/smartdocs_tests && scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests`，重新编译最后的停用词测试增强后完整回归 **127 passed, 0 failed, 0 skipped**，Result=success / ExecMainStatus=0 / ActiveState=inactive。结束后原 mysql.service=active，可用内存采样约 2.0 GiB；日志 `m2-search-regression.log`。定向 6 项属于全量 127 项的一部分，不相加作为新的分母。

本次使用 MySQL 官方 [ngram 文档](https://dev.mysql.com/doc/refman/8.0/en/fulltext-search-ngram.html) 核对自然语言分词及停用词边界。没有引入新依赖或迁移；默认测试配置使用 bigram 和内置停用词，不等于所有语言/配置搜索质量验收。普通 JSON 上限保留，并对搜索取 min(配置值,4096)。

日志临时根 `/tmp/smartdocs-safe.h4SzvX`（m2-search-red.log、m2-search-focused.log、m2-search-http.log），测试副本在其 repo 下。单元均使用上述非 root/CPU/内存/I/O 限额，临时 skip-networking MySQL；没有升级、停止或更改原有业务库。临时日志不是永久生产部署路径。

最终生产代码及测试集合指纹（包含新文件，不含文档），与执行副本逐文件 cmp 一致：

```bash
git ls-files -z --cached --others --exclude-standard -- code python test Makefile \
  | sort -z | xargs -0 sha256sum | sha256sum
# 391b8ce6e6ece4f4888a534bbe81ceaac00655fded8f22d19fe992db5aa04982
```

限制：故障观察点在候选读取后由另一数据库连接提交状态变化，覆盖已经提交的竞态，不宣称穷尽全部线程时序；真实 HTTP 使用稳定数据的两页结果，不承诺实时翻页期间不遗漏。没有重跑完整 M1 浏览器/上传中断验收，也没有最大规模搜索压力、搜索页面、向量/混合检索或远程 Agent 验收。schema 仍为 4，M2 仍未完成。
