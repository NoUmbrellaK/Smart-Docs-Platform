# M2 Bounded PDF Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 延续用户批准的方案 A；当前增量串行执行，不额外申请常规实施许可。

**Goal:** 本地提取文本 PDF，生成固定版本、物理页码及页内位置引用，在子进程硬限额内失败退出。

**Architecture:** 受控调用方传入 bytes；父进程校验输入并管理超时，独立 Python 子进程先设置资源限制再导入 pypdf、读取输入和提取。只接受有界结构化输出，复用已有文本分块规则；不接数据库、上传路由或远程模型。

**Tech Stack:** Python 3.10+、pypdf 6.21.0、Python <3.11 时 typing_extensions 4.15.0；wheel 哈希固定。

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md`

## Global Constraints

- 输入至多 8 MiB、300 个物理页、2,000,000 个规范化字符、2,000 个片段；每块默认 1,200 字符，页内分块，不跨页。
- 子进程虚拟内存最多 256 MiB、CPU 时间最多 10 秒、墙钟默认 15 秒；单页内容及嵌套 Form 解压总量最多 16 MiB，同一 Form 只计一次；输出临时文件最多 16 MiB；限制只可收紧。
- pypdf 无 OCR 能力。全部无文字返回 `no_extractable_text`；部分页无文字时返回 `pages_without_text`，不能宣称全页覆盖。
- 子进程用 Python isolated 模式、空环境、关闭多余文件描述符；不继承 API key。资源限制不是文件/网络安全沙箱，生产调用仍必须在非 root 受控服务内执行。
- 不修改 C++/数据库、不执行重型 M1 验收、不调用模型；只用公开模拟 PDF 小夹具。

---

### Task 1: 固定依赖和可验证的 PDF 提取

**Files:**
- Create: `python/requirements.txt`（固定 wheel 版本与 SHA-256）
- Create: `python/smartdocs/pdf_parser.py`（父进程输入校验、超时、输出检查和版本引用）
- Create: `python/smartdocs/pdf_worker.py`（先限资源再导入库，提取物理页文本）
- Create: `test/python/test_pdf_parser.py`（真实 PDF、真实子进程和限额测试）
- Modify: `README.md`（安装、测试、实际边界）

**Interfaces:**
- Consumes: `parse_pdf(data: bytes, *, file_id: str, version_id: str, limits: PdfLimits = PdfLimits()) -> PdfDocument`。
- Produces: `PdfLimits` 继承已有 `Limits`，增加 `max_pages=300`、`max_stream_bytes=16777216`、`timeout_seconds=15.0`；禁止超过上述硬上限。
- `PdfChunk` 继承已有 `Chunk` 并增加 `page`；页码从 1 起，字符/行号在每页重置；源哈希为整个原始 PDF；片段 ID 额外绑定物理页与固定 PDF 解析器修订。
- `PdfDocument(chunks: tuple[PdfChunk, ...], page_count: int, pages_without_text: tuple[int, ...])`；空白页和图片页无法仅凭空文本区分，不伪造 OCR 诊断。
- 错误复用 `ParseError`：输入/文本通用错误、`invalid_pdf`、`encrypted_pdf`、`too_many_pages`、`page_stream_too_large`、`no_extractable_text`、`pdf_timeout`、`pdf_resource_limit`、`pdf_dependency_unavailable`、`pdf_worker_failed`。异常消息不得包含原文、密码或内部路径。

- [x] **Step 1: 安装固定依赖，写失败测试**

创建仓库外临时 venv，以 `--require-hashes --only-binary=:all: --no-cache-dir` 安装，不改系统 Python。夹具复用已有 `test/fixtures/m1/sample.pdf`（PAGE ONE / PAGE TWO），其他夹具用 pypdf 在内存生成（空白页、加密、复制页面）。

```python
result = parse_pdf(sample, file_id='f1', version_id='v1')
assert result.page_count == 2
assert [(c.page, c.text.strip()) for c in result.chunks] == [(1, 'PAGE ONE'), (2, 'PAGE TWO')]
assert result.pages_without_text == ()
```

补充：重复解析稳定、文件/版本/页码/源字节绑定；空白第一页不让后页重编号；全部无文本、加密不可读、损坏；输入、页数、文本、片段、解压流边界；真实超时后终止并回收进程；子进程内存限额实测；清空环境且 isolated 启动；缺依赖/异常退出返回安全错误。

- [x] **Step 2: RED**

```bash
PYTHONPATH=python /path/to/venv/bin/python -B -m unittest discover -s test/python -p 'test_pdf_parser.py' -v
```

预期缺少 PDF 入口的断言失败；不把依赖未安装作为通过或跳过。

- [x] **Step 3: 实现受限提取和确定引用**

```python
# Worker: before importing pypdf or reading untrusted bytes.
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
# Respect any tighter inherited hard limit; never raise it.
```

父进程通过 stdin 传 bytes，stdout 写临时匿名文件并在退出后有界读取，stderr 丢弃（第三方库可能打印文件内容）；超时 kill/wait，不返回部分结果。页数/提取文字总量/解压流检查在子进程内；输出必须包含按物理页顺序的文本列表，父进程校验结构、总量后以现有 `parse_text` 切块，替换 PDF 来源哈希/修订/身份并增加页码。

- [x] **Step 4: GREEN 与审阅**

在当前服务器串行运行文本 + PDF 套件；测试进程另限 512 MiB 虚拟内存、90 秒墙钟。记录真实通过数量和资源限制探针，不声称生产沙箱、OCR 或后台接入已完成。执行 `git diff --check`，独立只读审阅此增量。

- [x] **Step 5: 文档、提交与正常推送**

更新 README 实际能力、依赖安装、测试命令和未完成事项；只推 `codex/m2-document-processing`，不合并 main。整个 M2 仍待持久任务、索引、检索及阶段验收。

## 依赖依据

- [pypdf 6.21.0 官方 PyPI 元数据](https://pypi.org/project/pypdf/6.21.0/)：支持 Python >=3.9；Python <3.11 需要 typing_extensions；固定 wheel SHA-256 已从官方 JSON 元数据核对。
- [pypdf 提取文本说明](https://pypdf.readthedocs.io/en/6.21.0/user/extract-text.html)：文本提取可能展开大量内容流；无 OCR 能力。仅限文件字节不足以保护宿主机。

## 实测记录（2026-10-17）

- 实现与 README 提交 `ad9ab97` 已正常推送到 `origin/codex/m2-document-processing`，未合并 main；提交前最后一轮仍为 28/28，3.564 秒。
- 独立 venv：`/tmp/smartdocs-m2-pdf.FLxk0l/venv`。本机缺 ensurepip，采用 `venv --without-pip` 后用系统 pip 的 `--target` 只安装到该 venv，未修改系统包；官方 PyPI 下载两个已核验哈希的 wheel，约 450 kB。
- RED：11 个 PDF 入口缺失断言失败。首轮 24/24 通过后补充错误边界，异常 Unicode 和过深 JSON 两项复现失败并修正；缺依赖场景通过，不跳过。
- 审阅发现 pypdf 会吞掉嵌套 XObject 提取异常，以及初版解压上限只覆盖页主内容。真实缺失/循环 Form 和压缩嵌套 Form 回归复现两项失败；按根因增加不保存消息的警告标记和嵌套流总量检查。两项定向复查通过。
- 最终完整纯 Python 套件 28/28（文本 13、PDF 15），无失败或跳过，3.519 秒；`git diff --check` 通过。真实子进程内存分配拒绝、CPU/输出限额读回、墙钟超时 kill/wait 均覆盖；没有进行 CPU 满载或最大文件规模性能测试。
- 当前仍是离线模块，不代表非 root 生产部署、C++ 鉴权接入或 M2 完整验收。下一增量为持久任务/后台 worker。
