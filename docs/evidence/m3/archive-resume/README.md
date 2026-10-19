# 真实确认后继续归档：验证记录

日期：2026-10-19。基线 `75db3310f5044d7ac09eff6a41b2be8c80d1ece2`，
分支 `codex/m3-mcp-agent`。本轮仅 Python、测试及文档变更；
C++/SQL未改，复用上一增量验证的三个二进制，指纹见 source-sha256.txt。
没有生产迁移/部署、付费模型调用、API key读取或资源限额提升。

## 范围与方法

先补失败测试再实现，原始失败保留在 red-unit.txt、red-e2e.txt、red-cli.txt。
真实链路另发现执行前改名冲突分类错误，保留 red-preflight.txt，并只修正错误映射。
独立只读审查及修复说明见 [review.md](review.md)。

本轮复用当前 ECS 的受限非root串行环境：smartdocsvectorcheck.slice，
CPU 100%、MemoryHigh 1200MiB、MemoryMax 1600MiB、无swap、TasksMax256，
/dev/vda读写各5MB/s和200IOPS；每单元RuntimeMaxSec900，退出回收整个测试进程组。
临时MySQL仅本机socket、InnoDB缓冲32MiB、禁PerformanceSchema；未访问原业务库。
每个HTTP测试类单独创建/迁移/清理临时库，防止固定用户夹具相互污染。

测试副本 `/tmp/smartdocs-safe.h4SzvX/repo`，工作树
`/root/Smart-Docs-Platform/.worktrees/m1-file-business`。
本轮源码、SQL、被执行测试逐文件比对一致；副本缺少两个依赖锁定清单，
使用已安装独立环境而非现场安装。未执行的旧M1浏览器自测副本有差异，
不属于本轮结果；本轮没有浏览器验收。

## 复现命令

以下命令在上述受限单元中串行执行，PYTHONPATH=python，
SMARTDOCS_TEST_PARSER_PYTHON 指向独立解析器环境；不能在低配服务器无限并发重跑。

```bash
/path/to/mcp-venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
scripts/test-with-mysql.sh -- /path/to/mcp-venv/bin/python -B test/e2e/m3_archive_resume_test.py
scripts/test-with-mysql.sh -- /path/to/mcp-venv/bin/python -B test/e2e/m3_archive_agent_test.py
scripts/test-with-mysql.sh -- /path/to/mcp-venv/bin/python -B test/e2e/m3_report_mcp_test.py
/path/to/parser-venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

归档继续套件覆盖：无key真实CLI未确认等待→确认→C++重启→执行→撤销批准后读回执；
响应丢失后恢复且不二次写；读取后修订竞争不切换新确认；取消；外发撤销；
其他任务所有者；本人降级reader；确认后改名预检拒绝。
单元测试另覆盖畸形预览/回执、操作三元组不匹配和未知未提交结果，禁止模型、finish或重试。
HTTP包装包含schema15迁移/重放、部分DDL拒绝及103文件旧库升级，不虚构历史批准/执行。

## 最终结果

最终受限单元 `smartdocs-archive-resume-green.service` 正常退出，MainPID=0、Result=success。
[完整日志](green.txt)包含：MCP/执行器 **77/77**、新增归档继续真实链路 **7/7**、
既有归档提案 **8/8**、报告链路 **4/4**、Python **106/106**；无失败/跳过。
同一测试在初始通过单元和最终回归中的重复运行不累计成更大的验收数字。

[资源快照](resources.txt)确认本轮所有测试进程退出，原业务MySQL仍运行；
memory.events high仍80521、max/oom/oom_kill仍0，即无新增节流或OOM。
不是持续监控采样，也不承诺任意组合负载稳定。

## 结论边界

本地脚本模型只验证动态编排，不证明真实供应商分类质量、准确率或完整五工具业务评测。
没有重跑全仓C++、浏览器或最大规模性能测试。继续控制器只提供显式可信本机入口，
不是网页调度器或后台自动恢复服务。剩余工作见[接口说明](../../../api/m3-archive-resume.md)。
M3及完整P0仍未验收，长期目标继续。
