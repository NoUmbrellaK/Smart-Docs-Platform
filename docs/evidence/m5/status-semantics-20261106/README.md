# 报告状态语义说明候选

2026-11-06，基线 `e73e375`。H06 首次实测已识别占位内容，却把部署状态写为 found。
修改前对原始私有快照运行[冻结状态断言](red-command.txt)，[实际失败](red.txt)为
deploy=found 与期望 uncertain 不同；其他五项一致。没有修改冻结用例或原始结果。

最小修改仅扩展共享 `DESCRIPTIONS[REPORT_TOOL]`，MCP 工具发现和模型工具定义共同使用：
found 表示读到相关实质资料，不代表完整或验收；有失败结果的实质测试报告仍可 found；
空模板、只有占位说明、未读/未解析候选及未解决冲突应 uncertain；
missing 只表示实际已检索范围未找到，未完成覆盖不等于不存在。
uncertain 可保留真实引用，不能因有引用就机械标 found。

没有新增 TODO 字符串拦截、自动重写状态、schema、权限规则或额外模型审查调用。
[AST检查](ast-check.txt)确认仅共享说明变化；[核验命令](ast-check-command.txt)比较归一化前后语法树。
这属于提示候选，不是服务端语义保证。不会把正文描述正确等同于结构化状态正确。

完整离线回归 **125/125，85.659秒**，见[命令](command.txt)、[日志与终态](journal-and-status.txt)。
局部 SDK 对未列出工具的警告来自既有负向用例，结果通过；没有跳过或删除失败测试。
未新增“包含指定提示字句”的变更探测测试；固定 H06 是语义失败用例，
真正效果需同输入真实复测，而非用脚本模型输出期望状态冒充修复。

[只读审查](review.txt)无阻断项，审查未运行测试。
[指纹核验命令](fingerprints-command.txt)、[运行中指纹](fingerprints-during-test.txt)、
[结束检查](postflight.txt)保留运行副本一致性：37份中仅 mcp_read.py 相对上一基线改变。
CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、IO5MB/s+200IOPS、Tasks256不变；
high仍82471、max/oom/oom_kill仍0，结束TasksCurrent=0，原MySQL active。
此离线候选步骤零付费请求，不改变30项台账；M5/P0未验收。
