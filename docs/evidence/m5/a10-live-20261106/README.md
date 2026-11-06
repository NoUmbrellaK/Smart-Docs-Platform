# A10：分类依据不足时保留待确认

2026-11-06，基线`7397f14`。仅新增测试观察和证据，不改产品、提示词或冻结输入。
沿用用户持续批准的同范围人工资料测试，本次一次真实运行，没有自动重跑。

## 实际结果

[结果](result.json)、[审计汇总](audit-summary.json)、[脱敏轨迹](trace.jsonl)：

- 准入201；DeepSeek `deepseek-flash`共2请求/2成功响应、5826 token：输入5407、输出419，
  cache hit3840、miss1567。每次实际输出上限1280，低于6请求×4096输出预算。
- 模型先读取`ambiguous@1`唯一分块，再创建归档方案。理由指出日历配色讨论没有需求、设计、测试或部署的分类依据。
- 方案修订1、preview；固定文件/版本/源目录对应本次夹具，目标null、逐项needs_review，
  has_unresolved=true、executable=false、decision和operation_id均为空。
- 清理前任务waiting_confirmation；独立SQL方案1、确认记录0。文件列表位置/版本不变，
  下载200，内容SHA-256与冻结内容一致；无execute_archive_plan调用。

这是冻结A10判据对应的边界观察，不是归档完成。人工业务判定留空；回答/报告引用为0，
不报告业务完成率或引用准确率。模型理由保留原文，文字细节仍可人工复核。
待确认状态在夹具清理前取证，随后夹具取消未结束任务并清理临时库，当前没有开放的审核现场。
本轮没有新增请求正文观察器，不能据此宣称取得原始序列化正文哈希或网络抓包证据。

## 验证和复现

[观察器](observe.py)复用A07逻辑，仅换用A10及`ambiguous@1`：读取任务、SQL计数、文件列表和下载哈希，
不改变模型请求或后端业务行为。[离线命令](offline-command.txt)复用既有预览采集测试并明确绑定A10，
[日志](offline-journal.txt)1/1通过、2.112秒；仅协议夹具，无模型调用，不计真实任务次数。

[真实命令](command.txt)通过LoadCredential/shlex私有加载key，CLI启动夹具前移除key环境变量；
固定`https://api.deepseek.com/chat/completions`，仅获准人工便笺和必要提示/工具结果外发。
本次无真实归档确认或执行；重复测试授权不代替具体方案确认。

[采集命令](collect-command.txt)、[只读提取](inspect.py)、[交叉核验](verify.py)及
[核验输出](verification.txt)核对私有/公开结果、完整审计、固定30项历史台账及文档链接。
私有快照、空白评审表、清理前观察保存在`/tmp/smartdocs-m5-a10-live.0nIzpW`，
目录0700/文件0600、nobody所有；规范化snapshot摘要
`92eecae6e4dc230810188136754fc260f05a5851eda9218d8ea8ced7b967e94f`。

## 环境和终态

原ECS 2vCPU/3495MiB，Python3.10.12、MCP1.30.0、httpx0.28.1；TXT关键词索引，无编码器。
串行smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
磁盘读写各5MB/s、200IOPS；nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax600秒。
[指纹命令](fingerprint-command.txt)、[前置](preflight.txt)、[后置](postflight.txt)共48条记录：
原47条基线记录不变，新增测试观察器一条；产品/冻结输入未修改。

实际运行内存抽样最低1612360KiB，非连续监测峰值；最终1764692KiB，TasksCurrent=0，原mysql active。
memory.events保持high82471，max/oom/oom_kill均0，无新增OOM。
[单元终态](journal-and-status.txt)19:39:38退出3，是CLI对waiting_confirmation的既有映射；
systemd的failed/NOTIMPLEMENTED是退出码标签，不是产品未实现、模型失败或服务器崩溃。

台账当前18 completed、1 failed、2 waiting_confirmation、6 not_run、3 admission_rejected；
累计35次尝试、123请求/618817 token、96条引用，人工判定全部为空，账单费用未查询。
M5/P0未完成：仍需A01–A06、具体方案真实确认/执行、H05已知偏差、语义评审、部署及完整演示。
