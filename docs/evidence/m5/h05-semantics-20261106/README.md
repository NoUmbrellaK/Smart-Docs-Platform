# H05 元数据检查语义澄清与第三次实测

2026-11-06，基线 `b6670d687cb685b49c2c00342fa09feffb8f3e7e` 加 [candidate.patch](candidate.patch)。
本次测试时补丁尚未提交，因此 metadata 明确记录基线加补丁，不伪装为已提交代码。
原问题仍是“本次只检查元数据搜索的第一页（最多10项），不翻页；报告必须说明没有完整核查。”
11份获批合成资料、六项清单、冻结预期和最多6请求×4096输出预算不变。

## 原因假设与改动

[原始失败](../h05-live-20261106/README.md)和[第二次失败](../h05-limits-20261106/README.md)
分别读取8次和6次正文；[全链路离线诊断](../h05-route-diagnostic-20261106/README.md)
已证明问题和分页反馈未丢失，工具没有自然语言元数据-only强制策略。
本轮假设模型将原句理解成只限制搜索页数、仍可追读候选正文；这是待验证解释，不是模型内部原因证明。
共享SYSTEM仅加四行，区分“只检查元数据结果/指定结果页”与“先定位候选再读正文”。
不改权限、工具、解析、预算或冻结输入，不添加H05特判和关键词拦截器。

[离线命令](offline-command.txt)与[日志](offline-journal.txt)：现有MCP回归125/125，85.852秒。
RED依据是两次已保存的真实失败，不冒称本轮重新执行了负例。
离线回归只能证明既有协议/模拟路径未回归，不能证明模型遵守自然语言。
独立只读审查检查两文件改动，无需修复项；明确需本次原输入实测后判断。

## 实际结果与边界

[摘要](result.json)、[审计统计](audit-summary.json)、[原始脱敏事件](trace.jsonl)：

- completed，保存私有draft；2次请求，7621输入+1261输出=8882 token；缓存命中2560/未命中5061。
- 工具顺序只有一次metadata search、一次save_handover_report；无正文/内容检索/后续分页调用。
- 搜索事件返回10项，报告11个selected_versions、10个source_versions；依赖与按file_id排序前10项一致。
  本次未包含conflict_b@1；新夹具ID导致各轮首页成员不同，不将结果差异唯一归因于提示改动。
- 六项全部uncertain、0引用，与冻结状态判据一致；summary/limitations明确未读正文、未翻页、不代表完整核查或验收。
- 本轮未保存原始模型请求/工具响应线包；不能用报告自述充当page_size/has_more/truncated原始响应证据。
  单次搜索、10项计数及依赖集合是本次独立可核对事实，分页协议字段另有先前离线证据。
- 报告仍把三个不同部署类文件名称作“命名冲突”，这是模型措辞，不是已证明的实际同名冲突；
  限制段又明确无法判断正文是否冲突。完整语义评审仍留空，不将这一观察升级为业务通过。

本次支持保留小范围语义澄清，但不证明长期稳定、其他问题不回归或存在硬性执行限制。
未启动额外重跑、归档确认/执行。H05三次累计10请求/63718 token，历史偏差不删除。
人工评审表保持空白，business_verdict/citation_accuracy/cost_estimate均null。

## 复核与资源

[启动命令](command.txt)仅向固定DeepSeek端点发送既有人工获批资料、问题/清单、系统提示与工具定义/结果；
用户“批准多次测试，不用审批”适用于这类有界测试，不扩大到真实业务资料或真实归档确认。
密钥通过systemd credential读取，仅用于认证，CLI在启动夹具子进程前移除环境中的key。
[采集命令](collect-command.txt)、[提取器](inspect.py)、[核验](verify.py)不调用模型。

[前置](preflight.txt)与[后置](postflight.txt)47条运行指纹一致；相对旧基线仅agent_runner变化。
候选补丁哈希一并记录，冻结JSON和其余运行实现未变。
原ECS2vCPU/3495MiB，Python3.10.12、MCP1.30.0、httpx0.28.1；TXT关键词索引，无编码器。
共享slice串行CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写5MB/s及200IOPS；
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax600秒；离线禁外网。
[终态](journal-and-status.txt)：退出0，TasksCurrent0，原mysql active，memory.events保持high82471、max/oom/oom_kill0。
运行时最低已记录可用内存抽样1599220KiB，不是连续峰值；无新增OOM。
私有原件 `/tmp/smartdocs-m5-h05-semantics.n9xpRJ/H05`，目录0700/文件0600、nobody所有。
规范化快照摘要 `b69475a6974a9b4367f66c894bb3fb1a382f9e0fd090e13973d9e6d2a93aeff3`。

固定30项最新状态仍18 completed、1 failed、3 waiting_confirmation、5 not_run、3 admission_rejected；
37次历史尝试合计127请求/634573 token、96条引用。费用金额未查询，人工业务判定为空，P0/M5未完成。
