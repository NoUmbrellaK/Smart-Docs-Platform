# 独立只读审查

2026-10-19，archive_execution_review审查者；基线378bd91及本轮未提交变更。
审查范围：方案/用户确认绑定、权限下降、取消与执行锁、全任务范围/版本/目标策略重验、
整批事务及幂等回执、等待/重启状态、既有任务生命周期、迁移与测试。审查者不运行重任务、不改文件。

首轮无Critical/Important，发现一项Minor：archive_decisions独立task_id和plan_id外键
没有确保二者属于同一方案。现有HTTP Match已阻止此路径，但直接修复/导入可能插入错配。
主代理用真实临时库直接INSERT复现：22项测试中仅该断言失败，CalledProcessError not raised。
记录见[schema-red-and-ui.txt](schema-red-and-ui.txt)，未用模拟数据库代替约束测试。

修复：archive_plans新增(task_id,id)唯一键；决策表改用(task_id,plan_id)复合外键。
不关联当前revision，保留合法旧修订决策。回归测试使用另一个无决策的任务，避免主键冲突掩盖问题。
审查者复核该差异，确认Minor已解决，未发现新的Critical/Important/Minor。
动态测试结果独立记录于本目录，静态审查结论本身不代替运行证据。
