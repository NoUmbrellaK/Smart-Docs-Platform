# S01 补充固定输入与执行入口

2026-11-06，基线 `48ad91e`。按需求13.2预先增加[独立清单](../../../../test/fixtures/agent-p0/supplemental-tasks.json)，
原corpus.json/tasks.json不变，原30项分母和历史96条引用保留。最新尝试只有49条引用，
不能用旧尝试的47条补足最新样本。S01单独统计，不代替未完成的原归档任务或人工评审。

S01使用已获准的人工deployment@2与rollback@1，问上线备份/启动顺序与回滚停写/成对恢复/重新开放，
要求分别引用来源，并说明资料未给出具体回滚触发条件。预先允许同义表达，禁止编造触发阈值或实际执行。
这是跨两份资料区分已知步骤和未给条件的任务，不是重复一次已有问题后挑成功结果。
预期依据点不是实际引用数；实测后统计全部输出，不承诺一定生成两条正确引用。

复用既有benchmark CLI、真实SQL/HTTP/stdio Agent、采集和评审；仅测试入口从两个固定本地清单
载入任务，拒绝重复ID和未知ID，不提供任意外部清单路径。原任务评审格式与两文件哈希保持兼容，
补充任务额外绑定supplemental-tasks.json哈希和独立dataset。expected仍仅用于离线判定，不发给模型。
产品实现、权限、模型提示、实际任务上限不变；真实运行仍需外发/费用授权，不能把输入冻结当成已执行。

新增S01评审用例先失败（原入口不识别S01），再修改入口通过；
CLI协议测试使用真实夹具与MCP，仅供应商HTTP响应为本地替代，验证原问题传递、双正文读取、
双来源采集和无自动确认/重试。该测试不是模型语义通过或收费调用。

## 验证与失败记录

[初次命令](offline-command.txt)错误地使用unittest模块发现把三个套件合并进同一数据库，
绕过execute/capture文件底部的专用入口，并加载继承的整套功能/固定输入测试。
[完整失败日志](offline-failed.txt)保留96项、74个失败和3个错误：
最先失败的归档断言读到此前CLI用例留下的1条decision，而不是预期0；随后固定输入检查读到8次已有模型计数，
后续类又复用worker-e2e-admin导致创建失败。后续idle等连锁失败不逐项断言根因。
新增S01在该轮本身已通过，但不能据此忽略其他失败。
改用[独立数据库串行命令](offline-isolated-command.txt)，每个文件使用自己的既有入口与临时数据库；
不删除测试、不放宽断言、不修改产品来适配错误启动方式。
[最终日志](offline-isolated.txt)：CLI 7/7（9.322秒）、执行器10/10（28.302秒）、采集器7/7（13.269秒），
合计24/24；之前失败的未确认归档用例通过。三次数据库初始化/迁移耗时不包含在这些测试计时内。

[轻量核验命令](static-command.txt)：评审测试[10/10](review-green.txt)，原固定输入[4/4](original-fixtures.txt)。
[历史兼容检查](historical-compatibility.txt)证明37次尝试/96条引用的JSON与Markdown仍逐字一致，
49条最新与47条旧尝试分开，五个损坏负向对照仍被拒绝，所有人工判定为空。
[运行指纹](fingerprints.txt)覆盖48条：相对H05基线仅5个测试入口变更，新增1个补充清单；
产品实现、原corpus/tasks不变，工作树与运行副本一致。
独立只读代码审查未发现需修复项；审查不替代实际SQL回归。

仍在原ECS上共享资源组串行执行：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、
读写5MB/s与200IOPS，nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、600秒预算；离线拒绝外网。
[采集命令](collect-status-command.txt)与[终态](final-status.txt)记录退出0、TasksCurrent0、原mysql active，
memory.events仍high82471、max/oom/oom_kill0。[后置指纹](postflight.txt)与前置完全一致。

本增量只冻结输入和测试接线，S01真实运行仍为not_run，新增付费请求为0。
后续使用已获准的同范围测试权限执行独立S01并另存结果，不回写冻结预期或原30项台账。
