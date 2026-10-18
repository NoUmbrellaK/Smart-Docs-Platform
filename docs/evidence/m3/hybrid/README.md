# M3 MCP 本地混合检索接线

2026-10-18，基线81570c5。复用现有查询协调器与私有Unix编码客户端，不新增依赖、
数据库迁移或权限逻辑。MCP和Agent可信本机入口增加可选`--encoder-socket`；
未配置时保持明确关键词降级。接口见[MCP查询边界](../../../api/m3-mcp-read-tools.md#本地hybrid查询边界)。

## 实际结果

- [审查后回归](reviewed-green.txt)：MCP/执行器 **43/43**（43.689秒），
  既有Python及新增deadline测试 **106/106**（20.336秒），单元成功退出。
  增量覆盖任务/项目两模式的先鉴权后编码、相同范围复查、预检查拒绝不编码、
  撤销或编码失败不返回旧结果、模型不能指定向量/socket/身份、非hybrid不编码，
  以及真实stdio子进程在挂起socket下超时后重新鉴权并返回关键词降级。
- [真实本地链路](e2e-journal.txt) **1/1**（测试本体12.891秒，不含数据库初始化）：
  官方MCP SDK stdio→C++→隔离MySQL→已有离线向量模型。中文查询“如何恢复备份？”
  以仅vector通道命中英文恢复资料；任务范围外的已批准文件与内部资料均未返回，
  coverage只统计范围内1份资料。缺失socket返回keyword_only及query_embedding_unavailable。
  `agent_cli.run_context`实际启动相同MCP子进程，接收真实hybrid结果并保存固定版本引用；
  之后撤销批准，新任务搜索拒绝，旧结果接口拒绝。调试工具已领取的任务先取消，
  Agent使用独立的真实pending任务，未伪造领取、授权或完成记录。
- Agent的大语言模型边界是本地脚本化函数，**没有DeepSeek/外部API调用或密钥读取**。
  向量模型是真实本地推理；不能把二者混淆，也不能把本例视为真实云模型质量评测。
- [编码器日志](encoder-journal.txt)显示真实READY、结束后正常停止。
  C++/Makefile/迁移源码与执行副本逐文件一致，副本`make -q bin/server bin/smartdocs-admin`
  exit0；复用[已有二进制](binary-sha256.txt)，本轮没有C++代码变更或重新构建。
  这是定向组合验证，不是全仓C++/浏览器全量回归。
- [六个源文件SHA](source-sha256.txt)与执行副本逐项一致。没有生产部署或原库升级。

## 失败与审查

第一次复制MCP测试到受保护执行副本被权限拒绝，导致首次选取hybrid用例为0项；
[初始日志](initial-sync-failure.txt)仅证明另一个短deadline测试1项RED，0项不是通过证据。
核实单元终止后授权同步，重新运行的[四项协议RED](protocol-red.txt)全部因缺少encoder接线失败。
实现后[中间回归](initial-green.txt)为42/42、105/105；不替代最终43/106。

[独立审查](review.md)发现connect后未重新扣减发送阶段时间，
[真实RED](deadline-red.txt)证明截止期后仍会发送查询字节。修复为每个阻塞阶段共享
同一monotonic截止期，并补CLI真实超时接线检查，最终回归通过，复审无阻断项。
最终日志保留既有未知写工具负例SDK诊断，以及一次异步测试慢任务提示；没有隐藏输出。

## 复现和资源

执行副本`/tmp/smartdocs-safe.h4SzvX/repo`；本轮串行单元前缀smartdocs-m3-hybrid-。
所有单元nobody/nogroup、Nice10、UMask0077、NoNewPrivileges=yes、KillMode=control-group、
TimeoutStopSec5。共享smartdocsvectorcheck.slice保持MemoryHigh1200MiB、MemoryMax1600MiB、
Swap0、CPU100%、Tasks256；/dev/vda读写各5MB/s、200IOPS。没有提高配额或清页缓存。
本地RED单元RuntimeMaxSec120、完整Python组合180；真实E2E780。

本地回归命令（在上述受限临时单元中串行）：

```bash
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
```

编码器另加PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、Type=notify、
NotifyAccess=main、TimeoutStartSec190、RuntimeMaxSec900；私有socket父目录0700，文件0600。
只有其实际READY后才运行业务单元，不用固定延迟冒充就绪。

```bash
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m smartdocs.resident_service \
  /tmp/smartdocs-m3-hybrid.Mc0e5w/encoder.sock \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
PYTHONPATH=python SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-m3-hybrid.Mc0e5w/encoder.sock \
  bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_hybrid_test.py
```

主机MemAvailable低于512MiB停止的护栏未触发；[最终状态](final-status.txt)可用内存
1731084KiB，原MySQL仍PID975。memory.events从high80272/max0/oom0/oom_kill0到相同值，
无新增内存节流或OOM。E2E和编码单元均MainPID0、成功退出，无本轮运行单元。
隔离MySQL及测试资料由既有夹具清理，socket已移除；空私有父目录和原模型保留，
没有业务数据删除。日志中的空密码警告只属于禁网络隔离测试库初始化，非生产配置。

## 剩余边界

编码socket10秒上限不延长Agent单工具30秒/执行180秒预算；慢后端可能使整步超时。
客户端断开不能承诺撤回已送达的本地计算，编码服务响应写入失败可能退出，需人工恢复。
不自动启用常驻服务。仍只有两个只读MCP工具；真实DeepSeek调用待明确外发批准，
内部HTTP认证、完整审计API、任务网页和三个写工具仍待完成，M3/P0尚未验收。
