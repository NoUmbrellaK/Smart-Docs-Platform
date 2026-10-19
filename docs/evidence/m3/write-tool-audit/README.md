# A18 五类后端工具轨迹

2026-10-19；基线 `815dc60602c732b3fb71c753f5c706967f74dc1c`，分支 `codex/m3-mcp-agent`。
契约见[后端工具轨迹](../../../api/m3-tool-audit.md)。复用agent_tool_calls与原生details，
仅增加schema16 CHECK扩展；未安装生产服务、迁移业务库、读取真实key或调用付费模型。

## 范围与故障语义

五类C++工具入口统一先持久化unknown，再执行业务。归档方案waiting_confirmation不代表移动；
真人确认仍是独立业务操作。已确认拒绝正常记状态；写操作5xx/未知异常不猜测事务回滚，
保留unknown并禁止自动重试。完成轨迹失败也返回不可重试503，持久结果通过原接口查询。
写结果永不进入来源台账。64条上限包含拒绝和重放，满后拒绝业务调用，GET回执仍可读取。

## 验证记录

初始RED：新增前端工具名/参数断言失败；真实HTTP既有读取4项通过、新写轨迹6项失败。
审查发现mysql_commit成功后恢复autocommit失败仍可能抛503，因此不能将写操作异常记录为确定失败。
另加数据库业务审计故障回归验证分类；此注入发生在提交前，不冒充复现提交回包丢失。
完成轨迹UPDATE故障则实际覆盖业务已提交而审计仍unknown的归档/报告恢复场景。

[完整回归日志](green.txt)：前端100/100、页面契约8/8、MCP/执行器87/87、既有Python106/106，
C++相关分组18/18、真实HTTP48/48通过，无失败/跳过。HTTP含既有读审计4、升级1、新写审计7、
既有归档22及报告14；覆盖拒绝、并发、完整回滚、确认、撤销、来源隔离和幂等重放。
[真实SDK归档4/4、报告4/4](integration-browser-start-failure.txt)通过，含实际stdio子进程；
同单元随后浏览器首轮会话启动失败，未进入页面业务断言。ChromeDriver报告
DevToolsActivePort不存在，检查时无新增OOM；根因未确定，不以关闭沙箱或提高限额规避。
保留原限额及沙箱的[独立复验](browser-green.txt)1/1通过，两次浏览器尝试合计1成功/1启动失败。
360/375/390/414/768/1280屏宽无轨迹区域横向溢出、summary至少44px高；真实UI展示等待、
拒绝和已提交但审计未知状态，折叠/展开/刷新不产生新写请求，授权撤销后读取轨迹被拒并清空。
[手机](write-audit-360.png)与[桌面](write-audit-1280.png)截图已人工复核。
不能据一次复验通过宣称间歇启动故障已解决。

[初始RED](initial-red.txt)、[审查回归RED](uncertain-write-red.txt)、[审查处理](review.md)，
以及[首次构建](build.txt)、[修复后构建](rebuild.txt)均保留。12个源码/测试文件与受限副本cmp一致；
[SHA256](sha256.txt)包含实际被测副本的三个二进制，不冒用工作树旧二进制。

## 复现与资源限制

在既有 `/tmp/smartdocs-safe.h4SzvX/repo` 副本执行，nobody/nogroup，单进程增量make -j1，
所有编译/测试串行于smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB、Max1600MiB、
Swap0、TasksMax256，磁盘读写各5MB/s及200IOPS；Nice10、UMask0077、NoNewPrivileges，
RuntimeMaxSec900、KillMode=control-group。MemAvailable低于512MiB停止本轮单元，不提高限额。

数据库包装器只创建临时socket库、禁TCP、32MiB缓冲；检验schema16与重放、部分DDL拒绝、
103文件升级。额外旧读轨迹升级测试在一次性库重建schema15 CHECK后迁移两次，核验历史逐字段不变。
Chrome使用既有匹配驱动和非root沙箱，不下载或关闭沙箱；页面fixture不是远程模型产物。

执行命令（PYTHONPATH为副本python和test/e2e；MCP/parser/Node复用已有隔离环境）：

```sh
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
node --test test/e2e/*_test.mjs
python -B test/e2e/ui_contract_test.py
python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
python -B -m unittest discover -s test/python -p 'test_*.py' -v
scripts/test-with-mysql.sh -- python -B test/e2e/m3_write_tool_audit_test.py
scripts/test-with-mysql.sh -- python -B test/e2e/m3_archive_mcp_test.py
scripts/test-with-mysql.sh -- python -B test/e2e/m3_report_mcp_test.py
scripts/test-with-mysql.sh -- python -B test/e2e/m3_write_audit_browser_test.py
```

后端合并运行采用同一WriteToolAuditE2E夹具，先test_audit_/test_write_audit_，再既有
test_archive_/test_execution_/test_report_，避免复用同库时重复创建固定测试账号。
C++分组为mysql_schema_、mysql_transaction_、agent_sources_、router_、http_connection_。
浏览器指定既有SMARTDOCS_BROWSER_BIN、SMARTDOCS_WEBDRIVER_BIN，以及独占0700的
SMARTDOCS_WRITE_AUDIT_BROWSER_OUTPUT目录；日志不收集真实凭据。

[资源终态](resources.txt)：本轮所有单元MainPID=0，共享组TasksCurrent=0，原mysql仍active；
high80521/max0/oom0/oom_kill0与基线相同，无新增节流/OOM。采样MemAvailable均高于512MiB，
采样不是峰值测量；原生产库和文件未改动。

本轮不是完整MCP/模型审计、固定任务质量评测、全仓C++回归或生产长期稳定性证明。
模型usage、Python本地拒绝、完整MCP耗时仍只在既有CLI本地审计，M3/P0尚未验收。
