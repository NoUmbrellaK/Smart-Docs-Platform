# 只读审查与处理

既有review agent对基线815dc606之后的变更只读审查，无自行修改/测试/外网/key访问。
发现一项实质问题：AuditedTool将写execute抛出的AppError/未知异常全部落为确定失败。
mysql.cpp的Commit在mysql_commit成功后恢复autocommit仍可能抛出retryable=true的503，
此时业务已提交而审计可能通过另一连接成功写成dependency_failed。

处理：先增加真实HTTP/隔离数据库503回归；修正写5xx/未知异常保留unknown并返回
retryable=false，要求读取持久结果；明确4xx拒绝仍正常落状态。测试不声称模拟真实网络提交丢包。
原有完成记录故障测试另行覆盖实际已提交写入及unknown轨迹。

其余所查未发现权限、容量、幂等或隐私回归：准入失败先于业务执行，容量经任务行锁串行，
写来源字段始终NULL，业务权限/确认检查沿用原实现，迁移保留历史仅扩展CHECK。

修复后只读复核通过上述错误分类。另纠正浏览器测试预期：撤销来源批准后，已提交归档
最小回执仍应可读；必须点击读取轨迹，才由ToolCalls的Scope复验拒绝并清理页面。
没有为测试修改持久回执的产品语义。
