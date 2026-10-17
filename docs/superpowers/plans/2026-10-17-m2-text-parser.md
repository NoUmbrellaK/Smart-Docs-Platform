# M2 TXT/Markdown Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 用户已选择方案 A 并要求常规细节自行执行；本增量顺序执行，减少上下文及资源消耗。

**Goal:** 提供离线、有界的 TXT/Markdown 解析函数，生成绑定固定版本且可精确定位的稳定片段。

**Architecture:** 纯函数接收 bytes 与文件/版本 ID，不读路径、不接数据库或远程模型。UTF-8 校验后规范化换行并连续切块，结果全成或明确失败；C++ 接入与后台 worker 不在本增量范围内。

**Tech Stack:** Python 3.10+ 标准库、unittest；零新增依赖。

**Spec:** `docs/superpowers/specs/2026-10-17-m2-document-processing-design.md`

## Global Constraints

- 文本默认上限：输入 8 MiB、规范化文本 2,000,000 个字符、每块 1,200 个字符、最多 2,000 块。
- 行号从 1 起；字符偏移 `[start, end)` 按规范化文本的 Unicode 码点计数。
- 不读密钥，不调用 DeepSeek，不进行数据库迁移，不重跑 M1 重型验收。
- 纯函数测试限制虚拟内存 256 MiB、CPU 时间 20 秒、墙钟 30 秒，串行运行。

---

### Task 1: 可引用的纯文本解析

**Files:**
- Create: `python/smartdocs/__init__.py`（包标识）
- Create: `python/smartdocs/text_parser.py`（校验、分块和来源位置）
- Create: `test/python/test_text_parser.py`（真实数据行为测试）
- Modify: `README.md`（当前状态、执行入口与未覆盖项）

**Interfaces:**
- Consumes: `parse_text(data: bytes, *, file_id: str, version_id: str, kind: str, limits: Limits = Limits()) -> list[Chunk]`，`kind` 为 `txt` 或 `md`。
- Produces: 不可变 `Limits(max_bytes, max_chars, chunk_chars, max_chunks)`；不可变 `Chunk(chunk_id, file_id, version_id, source_sha256, parser_revision, text, char_start, char_end, line_start, line_end)`；带 `code` 的 `ParseError`。
- 对外错误码：`invalid_input`、`unsupported_format`、`input_too_large`、`invalid_encoding`、`binary_text`、`empty_text`、`text_too_large`、`too_many_chunks`。

- [ ] **Step 1: 写失败测试**

测试先检查解析入口存在，避免把拼错 import 当业务失败。关键夹具与手算结果：

```python
chunks = parse_text(b'\xef\xbb\xbfA\r\nB\rC', file_id='f1', version_id='v1', kind='md',
                    limits=Limits(chunk_chars=3))
assert [(c.text, c.char_start, c.char_end, c.line_start, c.line_end) for c in chunks] == [
    ('A\nB', 0, 3, 1, 2), ('\nC', 3, 5, 2, 3)]
```

覆盖稳定 ID、换版本/文件/源字节防碰撞、中文长行、尾部换行、UTF-8 错误、控制字符、空内容、所有上限的等于/超过和非法参数。重组片段必须等于规范化全文，Markdown 中的指令只是普通文本。

- [ ] **Step 2: 运行 RED，确认失败源自缺少解析功能**

```bash
ulimit -v 262144
ulimit -t 20
PYTHONPATH=python timeout 30s python3 -B -m unittest discover -s test/python -p 'test_*.py' -v
```

预期：解析入口缺失的断言失败，未运行数据库或网络请求。

- [ ] **Step 3: 最小实现**

先校验真实类型（bool 不能作为正整数限制）、标识非空且不含控制字符、格式和输入长度；严格解码、规范化、校验空文本/控制字符/字符与片段总量，再返回全部片段。片段 ID 使用 SHA-256(JSON([解析修订, 文件 ID, 版本 ID, 源哈希, 起点, 终点]))。

```python
end = min(start + limits.chunk_chars, len(text))
part = text[start:end]
line_end = line_start + part[:-1].count('\n')
next_line_start = line_start + part.count('\n')
```

修订名固定为 `utf8-lines-v1`；换行在其前一行，避免块尾换行把引用错误扩展到未包含字符的下一行。最后一个片段不得产生空尾块。

- [ ] **Step 4: 运行 GREEN，检查输出与 diff**

重跑 Step 2 同一命令，预期所有测试通过、无跳过。执行 `git diff --check`。在 README 写明这不是已接入上传链路的后台解析，也不是 M2 验收完成。

- [ ] **Step 5: 审阅、提交、推送**

```bash
git add python/smartdocs/__init__.py python/smartdocs/text_parser.py test/python/test_text_parser.py README.md docs/superpowers/plans/2026-10-17-m2-text-parser.md
git commit -m 'feat: add bounded version-bound text parsing for M2'
git remote -v
git branch --show-current
git push -u origin codex/m2-document-processing
```

只推已验证的 M2 分支，不合并 main、不修改已关闭的 M1 分支。记录实测结果及遗留 M2 工作，不标记全项目目标完成。
