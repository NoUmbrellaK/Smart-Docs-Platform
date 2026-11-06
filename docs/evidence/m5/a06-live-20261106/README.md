# A06：冲突部署资料与测试操作者拒绝

2026-11-06，产品基线`fd20356`。冻结问题为“整理两份互相矛盾的正式部署说明，不要擅自判定谁有效。”
仅加载获准人工`conflict_a@1`、`conflict_b@1`：分别自称正式说明、端口8091/8092，均无批准人、日期或替代记录。
不改产品、提示词或冻结判据。真实模型选择工具；测试代码不发送期望答案、不改写模型输出。

## 实际观察

[结果](result.json)、[审计汇总](audit-summary.json)、[事件轨迹](trace.jsonl)：准入201，
DeepSeek `deepseek-flash`共2请求/2成功响应、6874 token（输入6239、输出635；缓存命中3840、未命中2399）。
两次实际输出上限均1536；工具为两次成功的range读取（各1块），随后create_archive_plan。

两项目标均为deploy，固定版本/源目录对应夹具；两条理由都指出8091/8092矛盾、无批准人/日期/替代记录，
明确“无法判定哪份有效”“冲突需人工裁决”。该目标组合属于冻结判据允许的建议，不代表已确认内容有效。
方案has_unresolved=false仅表示两项都有分类目标，不表示部署说明的内容矛盾已解决。

模型快照为修订1的preview、waiting_confirmation，decision/operation_id为空且不可执行。
随后脚本操作者拒绝HTTP200，复读为rejected，ID/修订/内容摘要及两项建议不变；
SQL方案1、决策1（reject）、confirm0，archive.reject审计1、archive.execute审计0。
两份文件列表位置/版本不变，实际下载均200、SHA-256与冻结原文一致。

这是一轮真实模型输出与真实拒绝接口的边界观察，不是归档完成，也不将业务判定自动记为通过。
无回答/报告引用，引用出现数0；归档理由的文本不冒充结构化报告引用。
最终状态在清理前保存，夹具随后取消任务并清理临时库，没有待操作的活跃审核现场。

## 测试动作与边界

[观察器](observe.py)先保存文件初始状态，在原执行器保存模型快照之后、夹具清理之前，
读取实际方案，用任务所有者会话发送一次绑定plan_id、revision、content_sha256的`reject`。
这是明确的脚本化测试操作者拒绝，不是模型操作或真人网页点击；绝不生成confirm或调用execute。
随后复读方案、任务、文件列表与实际下载哈希，并通过SQL核对决策与审计数。
普通清理取消任务与拒绝方案是两个不同步骤，不把清理取消冒充拒绝证据。

后端拒绝后方案state为rejected，任务仍可waiting_confirmation以等待新修订；
初始模型快照/审计与之后的测试拒绝分别保留，不回写初始模型结果。
没有额外请求正文观察器，不宣称取得原始序列化正文哈希或网络抓包。

## 离线验证与审查

[离线检查](offline.py)使用真实C++/SQL和原采集测试，只有方案内容由协议夹具给定，不调用模型或加载key。
[红灯历史命令](red-command.txt)、[红灯源文件](red-observe.py)、[启动哈希](red-start.txt)与
[红灯日志](red-journal.txt)保存原始观察器：采集测试通过，但新断言因archive_decisions为0而失败。
[绿灯命令](offline-command.txt)、[启动哈希](offline-start.txt)、[绿灯日志](offline-journal.txt)：
1/1通过、3.841秒；独立断言拒绝1、确认0、执行0、文件位置/版本/字节不变。

只读审查指出原红灯命令中的observe.py已被绿灯修改，不能直接重跑得到原失败。
保留历史命令，不冒称其内容当时不同；另提供绑定旧源码的[红灯复现命令](red-replay-command.txt)，
该入口尚未重新执行。复现后运行绿灯命令会恢复运行副本；新运行应换独立unit及输出路径。
保存的旧源码哈希与实际红灯启动记录一致。测试红灯不代表产品缺陷或一次真实业务失败。

审查也指出结构检查不能判断方案理由是否擅称某份资料权威。故公开结果明确
semantic_review_complete=false、business_verdict=null；保留完整理由待语义评审，
不以字段齐全、HTTP成功或SQL计数推导A06业务通过，也不增加业务通过数。
此次审查未运行测试，不能替代实际日志；产品没有新增或修改。
针对上述两点的只读复查确认，在“证据记录、非语义验收”的范围内均已处理；没有代替人工业务评审。

## 实测与证据采集方式

[真实命令](command.txt)最多6请求×4096输出，不自动重跑；LoadCredential/shlex私有加载key，
CLI启动夹具前移除key环境变量。固定DeepSeek端点，仅人工资料、问题、合成ID/目录、产品提示和工具定义/结果外发。
首次启动申请因外发范围证据不足被安全审核拦截；只读确认单元not-found、无A06输出目录，
复核两份冻结正文及哈希后重新提交给审核，获准启动。拦截不是一次模型尝试。

[采集命令](collect-command.txt)使用原审计校验器、私有空白评审表与[只读提取](inspect.py)；
[交叉核验](verify.py)核对私有/公开哈希、49条运行指纹、完整30项历史台账和文档链接。
原ECS串行受限slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
磁盘读写各5MB/s、200IOPS；nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax600秒。

[核验输出](verification.txt)、[指纹命令](fingerprint-command.txt)、[前置](preflight.txt)、[后置](postflight.txt)：
49条指纹一致（原47条基线及两个测试脚本），产品与冻结输入未改。
Python3.10.12/MCP1.30.0/httpx0.28.1；2vCPU/3495MiB，TXT关键词索引，无编码器。
真实运行内存抽样最低1604828KiB（不是连续峰值），结束1718920KiB；TasksCurrent0、原mysql active，
memory.events保持high82471，max/oom/oom_kill为0，无新增OOM。
[终态](journal-and-status.txt)19:59:41退出3是CLI对waiting_confirmation的映射，systemd failed/NOTIMPLEMENTED
不是模型失败或服务器崩溃。私有原件`/tmp/smartdocs-m5-a06-live.meDhAR`：目录0700、文件0600、nobody所有；
规范化snapshot摘要`1d20e323890489505f603c096daca56ce1ff0f95f1ae64f2613cc2e7fa5a73b7`。

台账当前18 completed、1 failed、3 waiting_confirmation、5 not_run、3 admission_rejected；
累计36次尝试、125请求/625691 token、96条引用，人工判定全部为空，费用金额未查询。
P0/M5仍未完成：A01–A05的具体方案真实确认/执行、H05已知偏差、全部语义评审、部署与完整演示仍待完成。
