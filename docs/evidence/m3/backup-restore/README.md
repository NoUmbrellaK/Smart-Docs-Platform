# A26：隔离数据库与文件存储恢复演练

2026-10-19，基线 `c0c53b6f56acdfe60044b53d5f3d200cfedded7a` 加本提交。
仅新增 [测试脚本](../../../../test/e2e/m3_backup_restore_test.py) 和运维文档；
复用既有真实 HTTP、MySQL、上传/解析/报告测试，不新增产品代码、依赖或迁移。
这是 T-20 的关键词恢复部分证据，不是 M5 或生产灾备验收。

## 演练范围

使用 `scripts/test-with-mysql.sh` 新建的临时 MySQL/socket，schema16。
人工资料包含两个项目、管理员/编辑者/阅读者、逻辑目录、三个文件/四个版本、
一个私有报告及固定版本引用、一个运行中的任务和一个只确认首分片的上传。
报告由测试直接构造并通过真实工具回执准入/保存；准入计数不是实际模型调用或效果评测。

1. 完成同步本地解析，停止测试 C++ 服务；没有并行 worker、Agent 或清理写入方。
2. 使用 MySQL 原生逻辑导出，再复制完整 storage（objects、staging 及其他文件）。
   备份目录/文件权限限制为 0700/0600；使用流式 SHA-256 比较文件清单。
3. 新建 `smart_docs_restore_test`，不使用 `IF NOT EXISTS`、不覆盖已有库；
   导入备份并复制到新的 storage 根。启动恢复服务前，逐文件核对存储，
   比较原库/恢复库按主键排序的数据导出 SHA-256，覆盖所有表，而不只比较计数。
4. 在恢复库删除并重建 `document_chunks.ft_document_body` ngram FULLTEXT。
5. 启动独立环回 C++ 服务，核对 ready、登录/持久会话、项目角色、目录/文件列表、
   归档策略、交接清单、四个版本的原始字节与解析状态、关键词候选内容和当前版本过滤。
   不要求重建前后相关度排名相同；按 chunk ID 排序后比较完整候选及覆盖/告警字段。
   新版本仍未批准外发；阅读者不可写目录，编辑者/阅读者不可读取另一项目。
6. 私有报告 JSON/Markdown、固定来源 URL 和片段内容保持一致，重放保存仍为同一回执；
   同项目管理员/编辑者不能读该阅读者的报告。另一文件的历史片段仍定位旧版本，
   不能用新版 ID 读取旧 chunk；撤销未引用但实际准入来源后，报告和导出被拒绝。
7. 运行任务/上传恢复为 interrupted；已确认分片保持，补传剩余分片后下载哈希一致，
   重复 complete 返回相同文件/版本/处理任务，不产生重复业务结果。
8. 恢复侧修改后再次核对原库行快照、原 storage 和备份 storage 不变；
   停止恢复服务并重启原测试服务，原报告仍可读。只有全部检查后输出成功记录。

完整临时测试库、备份和 storage 由现有测试清理机制删除，不保存 SQL dump、会话或密码。
证据只保留安全的摘要哈希、数量、测试结果和资源状态；没有真实用户文件或凭据。

## 实际结果

- [首次运行](first-run.txt)：1/1 失败，5.050 秒，尚未开始备份。测试把 ngram
  自然语言检索误当作字符串精确匹配，要求只有两个候选；修正为必含两份目标资料、
  候选只能是当前版本且绝不含旧版。没有修改产品检索行为。
- [第二次运行](second-run.txt)：1/1 失败，23.906 秒，整库/存储/版本恢复已核对，
  失败在检索列表顺序精确相等。源码按 FULLTEXT 相关度排序；不应把排名不变作为恢复契约。
- [最终复验](verified.txt)：**1/1 通过，22.509 秒，失败/跳过 0**。
  在比较完整候选内容和覆盖/告警字段后，明确记录 `keyword_order_changed=true`；
  9 个存储文件、4 个版本、1 条报告引用及 1 个续传均通过实际 HTTP/SQL 核对。
  三轮是同一场景的调试与复验，不累加为三个验收场景；包装器迁移检查也不算恢复用例数。
- 只读审查未发现隔离或业务断言的重要缺陷；修正了过早输出成功标记，
  并给部署手册备份示例添加失败即停子 shell。该示例另经 6 个纯替身控制流检查，
  未执行其中任何真实生产命令；语法及 `git diff --check` 通过。

[环境](environment.txt)：最终单元从北京时间 22:52:44 至 22:54:48，
包括隔离 MySQL 初始化/迁移/清理，不能把 22.509 秒视作生产恢复时间指标。
所有单元 MainPID=0、slice TasksCurrent=0，原 MySQL 保持 active；
memory.high 事件仍为 80521，max/oom/oom_kill=0，无新增 high/OOM。
实际 dump 的行摘要仅用于同次原库/恢复库比较，随机 ID/时间使不同运行的摘要不同。

## 运行方式

必须在既有 `smartdocsvectorcheck.slice` 下串行执行，禁止在该 ECS 无限制直接启动：

```sh
# 工作目录为可由 nobody 读取的独立测试副本；不是生产 checkout/storage。
PYTHONPATH=python:test/e2e SMARTDOCS_TEST_PARSER_PYTHON=/path/to/parser-venv/bin/python \
  scripts/test-with-mysql.sh -- /path/to/mcp-venv/bin/python -B test/e2e/m3_backup_restore_test.py
```

实际运行采用 uid=nobody/gid=nogroup、Nice=10、UMask=0077、NoNewPrivileges=yes、
RuntimeMaxSec=900、TimeoutStopSec=5、KillMode=control-group，IPAddressDeny=any、
仅允许 localhost。共享 slice 单核、MemoryHigh1200MiB/Max1600MiB、SwapMax0、
磁盘读写各 5MB/s 和 200IOPS；不提高限额、不重编译、不停止原 MySQL。

## 未覆盖与限制

- 未验证向量索引重建、向量模型冷缓存恢复或真实混合索引灾备；不能宣称 T-20 全部关闭。
- 未执行生产恢复、异机/跨 MySQL 版本迁移、在线不停写快照、加密异地备份、RPO/RTO 或容量性能测试。
- 这不是通用备份管理工具。恢复演练中的 grant 仅授予临时测试账号；正式部署须单独验证权限与秘密配置。
- 不保存/恢复真实 API key，不发模型请求，不消耗付费 token；30 项语义业务任务仍未执行。
- 未对产品做修改，因此没有虚构的产品 RED→GREEN。测试自身的失败及最终复验如实保留。

正式运维注意事项见[部署手册](../../../deployment/m1-single-host.md#备份与恢复)。
