# 只读审查与失败修正

2026-10-19，独立只读审查覆盖本轮 Python 执行器/CLI/测试差异；
审查者未修改文件、运行测试或访问生产数据。首轮未发现阻塞的授权、幂等或恢复问题。

主执行者随后补入真实预检改名用例：C++ 正确返回409 archive_plan_changed，
Python 却落入 dependency_failed；7项真实链路测试中该项失败，其余6项通过。
见 red-preflight.txt。修复仅增加 archive_plan_changed → scope_changed 映射，
不改 C++ 权限或事务逻辑、不放宽确认条件。

补充只读审查确认：预检异常发生在 attempted 标记之前，不调用执行工具、
不重试、不写 finish；角色降级仍由 C++ 拒绝执行，返回等待状态和 access_denied。
未发现新的阻塞问题。静态审查不代替运行测试，也不是全仓安全审计。

其他先失败后实现记录：red-unit.txt（缺少继续控制器，6项失败）；
red-e2e.txt（缺少 CLI/会话模式，5项失败）；red-cli.txt（等待状态吞掉错误，1项失败）。
这些是测试驱动开发中的预期失败，不与最终回归累计为成功数量。
