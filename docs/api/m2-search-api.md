# M2 本地搜索 API

此接口用于已登录用户在本机阅读资料，不是 MCP/Agent 外发入口。普通内部资料也可检索；未来 Agent 必须走额外的具体版本外发检查，不能直接把此接口的名称、计数或正文转发给模型。没有向量请求，不声称混合检索已完成。

## 请求

`POST /api/v1/projects/{project_id}/search`。最低 reader，沿用会话 Cookie、同源 Origin。JSON 上限为 `min(SMARTDOCS_MAX_JSON_BYTES, 4096)`；URL 查询参数不接受。全部值通过预处理参数绑定，不拼接用户 SQL。

```json
{"mode":"keyword","query":"部署","page":1,"page_size":10}
```

| 字段 | 约束 |
| --- | --- |
| mode | 必需：metadata 或 keyword；不支持的 hybrid 不会被伪装为成功 |
| query | 必需：有效 UTF-8，最多 256 字节，不允许 ASCII 控制字符；keyword 不能空/全 ASCII 空格；metadata 可空以分页列举 |
| directory_id | 可选：本项目 32 位小写十六进制目录 ID，只匹配当前所在目录，不递归 |
| media_type | 可选：完整 MIME，沿用上传校验并转小写，精确匹配记录的 MIME；不按扩展名推断 |
| page | 可选：整数 1–1000，默认 1，不接受布尔/负数/浮点数 |
| page_size | 可选：整数 1–20，默认 10 |

metadata 使用名称子串匹配，`%`、`_`、引号不是通配符或 SQL 操作符；名称比较沿用数据库 utf8mb4_0900_ai_ci 排序规则。未解析、不支持、失败或内容 unavailable 的当前文件仍可被发现，返回实际状态而无虚构正文。软删除文件排除。

keyword 使用 ngram FULLTEXT 自然语言模式，以相关度降序、片段 ID 稳定打破同分；metadata 按文件 ID 排序。仅纳入当前、未删除、available 且解析/关键词发布完成的版本。相关度不是置信度，不输出原始分数。

## 响应与来源

成功 HTTP 200，外层 `{request_id, data}`。data 包含：

- `mode`、`retrieval`：metadata 或 keyword_only，绝不宣称 hybrid。
- `items`：至多 page_size 条。metadata 每条一个文件；keyword 每条一个片段，同一文件可有多个片段。
- `page`、`page_size`、`has_more`、`next_page`：候选页有更多时 has_more=true；通常 next_page=page+1，无更多为 null。到第 1000 页仍有更多时 next_page=null 并提示 page_limit_reached，需收窄条件。
- `truncated`：还有未读候选，或选候选后有结果失效被过滤。它不是“每个片段正文被截断”的标志。
- `coverage`：current_files、keyword_ready_files、unsearched_files、counts_are_snapshot=true。计数限于当前项目、目录和 MIME 条件下未删除的当前文件，**不按 query 过滤、不是匹配总数，也不是全局文件数量**。unsearched_files=current_files-keyword_ready_files，描述正文关键词尚不可搜索，不否认 metadata 可发现这些文件。计数来自此次事务初始快照，后续状态可能改变。
- `warnings`：机器可读限制说明，调用方应显示或传递，而不是输出“已找到全部资料”。

每个 item 包含 `file_id, version_id, name, directory_id, media_type, parse_state, keyword_state, vector_state, availability_state`。keyword 额外返回：

- `chunk`：与 [固定版本来源 API](m2-processing-api.md) 一致的 chunk_id、file/version ID、source_sha256、parser_revision、text、char_start/end、line_start/end，PDF 还有从 1 起的物理 page。
- `coverage: {page_count, pages_without_text}`：单个文档的 PDF 覆盖情况。无文字页不能当作已检索正文；TXT/MD 为 0/[]。

chunk 原样返回已发布的最多 1200 个 Unicode 字符，不返回 HTML 高亮，不执行文档指令。前端使用 textContent 等安全方式显示，通过固定 version_id/chunk_id 打开来源；不得跳转新版替代引用。后续打开/下载仍重新鉴权。

## 实时状态与分页限制

事务先持有项目成员共享锁，仅选有限候选 ID；随后按文件 ID 排序加共享锁，并以当前锁定读重新检查项目、当前版本、删除、目录、名称/MIME、availability 和发布状态，再读取不可变片段。候选索引不是访问授权，初始快照中的旧版不能直接返回。成员/文件锁持有到提交，按这一事务边界判定读操作；不能撤回已返回给用户的数据。

分页是实时 offset 分页，不是冻结的搜索快照：期间新增、重命名、移动或索引变化可能造成跨页重排/重复/遗漏。始终有 live_pagination 警告；候选在重新校验时失效则过滤并增加 scope_changed，此页可能不足 page_size，客户端可重新搜索，不可据此断言材料齐全。has_more 来自初始候选，不承诺后页一定有有效结果。

查询不缓存，不创建新的后台任务或修改文件。候选结果缓冲最多 21 个 ID；元数据计数/候选 SELECT 设 5 秒 MySQL 执行提示，错误按既有 503 依赖故障处理。这个提示不是整个 HTTP 请求的硬墙钟保证，也没有证明大规模吞吐。

## 检索边界与错误

keyword 始终提示 ngram_stopwords_apply 和 vector_search_unavailable；有未就绪文件时提示 unindexed_content_not_searched。MySQL 默认 ngram_token_size=2，短于 token 的词可能无法命中；停用词影响索引，查询自然语言模式会按 ngram 分解匹配，不是精确全文子串。见 [MySQL 官方 ngram 文档](https://dev.mysql.com/doc/refman/8.0/en/fulltext-search-ngram.html)。改变分词/停用词配置后必须重建和重验索引，不能仅改变量就宣称旧索引已更新。

零命中仅表示“在当前可访问且已检索的资料中未找到”；不等于文件不存在、正文完整覆盖或业务验收通过。metadata 空查询可用于发现尚未解析的资料。本接口没有 OCR、语义召回或跨片段短语保证。

缺失/无效会话沿用 M1；非同源 403；无项目权限、外项目/不存在目录 404 resource_not_found；字段、模式、类型、UTF-8、长度及分页错误 400 invalid_request；正文超限 413 body_too_large；数据库忙或不可用 503。未知参数（如 user_id、approved、路径或模型参数）一律拒绝。

实现和实际测试证据见 [搜索增量计划](../superpowers/plans/2026-10-17-m2-local-search.md)。这不代表搜索页面、Agent 或完整 M2 验收已经交付。
