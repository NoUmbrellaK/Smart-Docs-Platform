# M4 当前代码边界复验

2026-10-20，被测提交 `ab620f5698ffba4997034ad33ab7dbb68cc5af91`。
只运行现有测试，不修改生产代码、测试断言或依赖；没有远程API调用、密钥加载或生产迁移。
按最小实现原则复用已有隔离数据库与测试入口，不重新搭建M4服务。

## 实际结果

[完整日志及清理终态](./journal-and-status.txt)：

| 套件 | 实际结果 | 直接覆盖 |
| --- | --- | --- |
| m3_archive_execution_test.py | 22/22，27.360秒 | 9项策略/预览及13项确认/执行；未确认拒绝、旧修订拒绝、权限与范围变化、整批回滚、并发幂等、响应丢失与重启回执 |
| m3_report_mcp_test.py | 4/4，7.071秒 | reader保存报告、固定来源及未引用依赖撤销、非法引用/操作/所有者拒绝、取消不写、已提交丢响应后不重复保存 |

两套均0失败、0跳过；每套使用独立临时MySQL，避免共享固定账号夹具污染。
报告测试中的模型决策为本地脚本，不是DeepSeek；四项均使用官方SDK，主要调用在进程内，
其中test_mcp_report_invalid_citation_owner_and_operation_are_rejected最后一次合法保存
经过真实stdio子进程，业务后端均为真实C++/SQL。
报告语义准确率、真实模型分类质量、完整业务演示与30项业务评测不在本次分母内。
本次也没有重跑浏览器、向量模型、全仓C++或完整Python单测。

## 环境与复现

[启动前核对](./preflight.txt)比较193个受跟踪文件，差异仅为副本缺少两份依赖清单
以及本轮不执行的旧M1浏览器自测；其余190个文件逐字节相同。
使用已安装的锁定环境，不进行依赖安装；被执行脚本、业务源码、wrapper与当前工作树一致。
服务器及管理二进制SHA256、两个测试脚本和Agent页面指纹见该记录。
没有重新编译；复用此前通过验证且哈希一致的安全副本二进制。
首次普通沙箱读取副本某测试文件被权限拒绝，随后只读提权完成核对，未放宽文件权限。

单元 `smartdocs-m4-boundaries-20261020`，21:34:48至21:38:53 CST，包含两个限速临时库的
初始化、迁移检查及清理；表中时间仅为各测试本体，不是业务响应SLA。
[运行中配置](./running.txt)确认nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、
900秒运行上限、5秒停止超时、control-group回收，以及只允许回环的IP限制。
原slice限额不变：CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0，
磁盘读写各5MB/s和200IOPS。本轮没有设置EnvironmentFile或读取DeepSeek配置。

工作目录 `/tmp/smartdocs-safe.h4SzvX/repo`，
PYTHONPATH为该目录的python及test/e2e，
SMARTDOCS_TEST_PARSER_PYTHON为 `/tmp/smartdocs-safe.h4SzvX/venv/bin/python`。
仅在上述限额与禁外网单元内串行执行：

```sh
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_archive_execution_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_report_mcp_test.py
```

临时MySQL仅Unix socket，32MiB缓冲池、无performance_schema；两套均通过schema16迁移检查。
空root密码警告仅来自隔离测试库初始化，不涉及业务数据库。
启动前MemAvailable2235272KiB、保存的运行中采样2149964KiB、结束2208248KiB；
不是连续最低值或峰值RSS。memory.events前后high80521、max/oom/oom_kill均0，
无新增内存节流/OOM，未触发512MiB停止阈值。
最终MainPID=0、slice TasksCurrent=0、原mysql active；成功临时单元被系统自动回收。
wrapper清理了自己的临时库与人工样本，不能直接恢复；未删除业务资料或原有审计。

## 结论

现有后端确认/事务及报告来源边界在当前副本通过上述复验，不代表M4完整验收。
[阶段核对](../stage-gate-audit.md)及独立只读审查发现方案新修订没有网页操作入口；
下一增量补该路径，不能拿后端通过或测试夹具PUT替代用户调整流程。

归档检查：原始日志中22与4项结果、26个ok条目、退出及资源计数一致；
185个本地链接目标存在，凭据模式扫描及`git diff --check`通过。
这些检查不增加产品测试分母，也不改变M4未关闭的结论。
