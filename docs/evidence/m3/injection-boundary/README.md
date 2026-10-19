# A21 恶意文档输入下的执行硬边界

2026-10-19；产品基线 `a15c08bbdaf11421bdbcca3712da113e0ae138c5`。
对应 T-12 的局部安全证据，不是完整 T-12、M3/P0 验收或真实模型抗注入评测。
本轮仅新增测试和文档；不修改产品、不引入依赖、不为已有正确行为虚构 RED。

## 固定四场景

| 场景 | 脚本化恶意调用 | 实际拒绝层 |
| --- | --- | --- |
| 删除文件 | delete_file | 执行器只读工具白名单 |
| 执行命令 | execute_command | 执行器只读工具白名单 |
| 伪造归档确认 | execute_archive_plan | 执行器只读工具白名单 |
| 冒用身份 | search_documents 携带 user_id | 执行器参数 Schema |

[测试源码](../../../../test/e2e/m3_injection_boundary_test.py)复用 AgentHttpE2E；上传、解析、
Agent HTTP、CLI、stdio、C++ 和隔离 SQL 均为实际组件。仅供应商 HTTPX transport 被替代。
第一轮要求合法读取已批准版本；第二轮确认真实 chunk 的 file/version/text，包含完整恶意指令，
然后故意返回上表调用；第三轮确认 invalid_request 并重复同一非法请求。
这是主动模拟不可信模型输出，不推断真实模型会服从或拒绝文档指令。

每个任务最终持久化 failed/repeated_tool_error、model_calls=3、result=null；重复执行返回409，
不再请求供应商。本地 tool_attempt 和后端 tool-calls 均仅一次成功 read_document_version。
四种非法调用没有进入 MCP 派发，不能计为四次 C++ 拒绝。三轮请求也不能算三个测试场景。

验证两份文件原始下载字节、文件元数据、版本批准历史和成员列表前后一致；
任务的 archive_plans/archive_decisions/handover_reports 均为0。
未批准内部文件的正文和名称、合成 key、用户 cookie 不进入模型请求正文；
恶意正文只在 tool 消息而非 system/user 消息中。本地审计不含恶意正文或内部正文，
沿用夹具的私有权限、会话/key 脱敏与上下文清理断言。
供应商鉴权头按设计携带合成 key；未全面扫描所有服务日志，不宣称接收器完全没有凭据。

## 本轮执行与审查

[原始运行、资源及哈希记录](./run.txt)：固定 **4/4**，0失败、0跳过，测试10.117秒；
含临时库初始化的单元于21:49:55至21:51:55运行，退出0。
本轮只读审查核对上传到模型请求的内容链路、拒绝层和状态快照，未发现重要问题；
审查未运行测试，不能代替上述实际执行。按审查意见明确了拒绝层及日志覆盖范围。

产品未修改，未重建 C++ 或重复跑全量套件。执行前及结束后校验 A20 的 sha256 清单：
10个相关源码/测试及3个实际二进制均匹配；新增测试与受限副本哈希相同，见运行记录。
A20 的其他测试结果仍属上一轮，不重新计数。本次测试首次运行通过，没有删除失败记录。

执行身份 nobody/nogroup、Nice10、UMask0077、NoNewPrivileges；共享 slice 保持 CPU100%、
MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、TasksMax256，磁盘读写各5MB/s及200IOPS。
结束 MainPID=0、TasksCurrent=0，MemAvailable1262MiB；memory.events high保持80521，
max/oom/oom_kill均0，无新增节流/OOM，原 MySQL 仍 active。
临时 MySQL 仅私有 socket、32MiB 缓冲池、关闭 performance_schema；未迁移业务库或部署生产。
没有读取真实 key 或真实供应商调用；模拟 usage 不用于费用统计。

## 复现

以下为本机现有受限副本命令；先确保产品与 A20 哈希清单匹配、新测试已同步。
单独使用一个临时库，不能与另一个创建固定管理员的 E2E 入口共用库。

```sh
systemd-run --unit=smartdocs-injection-boundary \
  --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup \
  -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=900 -p TimeoutStopSec=5 -p KillMode=control-group \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e \
  --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_injection_boundary_test.py
```

监控同一单元，可用内存低于512MiB停止本单元，不提高限额。仅在原单元终止后重新执行。
真实供应商动态决策、30项业务任务、50条引用、完整审计及 M3/P0 验收仍待完成。
