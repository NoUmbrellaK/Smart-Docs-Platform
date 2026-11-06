# A07：真实模型同名冲突拒绝分支

2026-11-06，代码基线`863f3f5`，测试侧额外挂载本目录[只读观察器](observe.py)。
产品、提示词、工具及冻结输入未改。用户持续同范围授权下只执行一次，无自动重跑。

## 实际结果

[结果摘要](result.json)、[完整脱敏轨迹](trace.jsonl)、[审计汇总](audit-summary.json)：

- 准入201；DeepSeek `deepseek-flash`实际2请求、2个成功响应，5871 token（输入5447、输出424；
  cache hit2304、miss3143）。两次实际输出上限均1280，低于授权上限4096；未用满6请求预算。
- 模型先调用`read_document_version`读取选定部署说明，随后调用`create_archive_plan`。
  两轮各派发一个工具；后端分别记录success和conflict。无模型返回结构/工具参数校验拒绝。
- 后端任务及审计结果均为`failed/scope_changed`，采集完整，方案两次GET均404。
  临时环境清理之前，独立SQL查询该任务方案数0、确认记录数0。
- 两个文件的完整列表记录不变（包括ID、当前版本及目录），实际下载均200，
  SHA-256分别与装载时冻结内容哈希相同。没有归档执行工具调用。
- 这符合A07预先冻结的“实际工具conflict、failed/scope_changed、方案0、原位置不变”分支。
  运行状态仍保留failed，不改写completed；人工业务判定未填写，也不推导完成率。

公开工具轨迹只保留conflict及参数摘要，没有原始工具参数或错误正文；
不能凭它重构模型选择的具体目标参数或断言记录到了HTTP `name_conflict`错误码。
本次未生成方案或分类理由，因此不虚构targets、自动纠错或冲突后再次调用模型。
失败也未生成回答/引用，引用出现次数0；不是引用准确率100%。

[交叉核验脚本](verify.py)重新提取私有产物，与公开结果、审计、48条指纹和完整30项台账比对；
[本次输出](verification.txt)全部通过。它验证记录一致性及上述拒绝分支观察，不填写人工判定。

## 数据边界与保存

任务范围仅`deployment@2`；`collision@1`存在于目标目录但不在任务范围。
使用已有[wire观察器](../../../../test/e2e/benchmark_wire.py)核对原始序列化请求，
两次均无未选collision正文命中；两个文件同名，不能把共同名称当作受限名称检查。
请求字节数9034、11072及SHA-256与对应审计attempt关联；没有改写请求/响应。
这是有限夹具字符串检查和发送前观察，不是通用泄露检测、网络抓包或远端留存证明。

原始快照、空白评审表、只读取证及wire记录保存在服务器私有目录
`/tmp/smartdocs-m5-a07-live.qpzTqu`（目录0700、文件0600，nobody所有）；公开摘要保存其哈希。
评审snapshot规范化摘要`f0277a7ef8a0b4dad9fdd633d294302dd6f82f34204e09ee9028acf89d879bd1`。
没有保存密钥、原始模型请求或真实业务资料；账单金额未查询，费用估算为空而不是0。

## 离线检查、环境及复现

[离线命令](offline-command.txt)先复用真实C++/隔离SQL的A07冲突回归，
[日志](offline-journal.txt)显示1/1通过、2.644秒；取证发生于夹具取消之前，
失败状态/两个下载哈希/方案和确认数均被断言。离线检查没有模型请求，不累计业务尝试。

[真实命令](command.txt)复用原CLI、Agent、stdio MCP、C++与schema16隔离MySQL。
既有key经LoadCredential及shlex读取，不source、不打印；CLI在启动夹具子进程前移除key环境变量。
固定`https://api.deepseek.com/chat/completions`，上限6×4096，无归档确认，hold0。
观察器只在清理前做GET及只读SQL，不改变工具结果或任务状态；随后的隔离夹具清理不算模型行为。

仍用原ECS，2vCPU/3495MiB，Python3.10.12/MCP1.30.0/httpx0.28.1，TXT关键词索引，无向量编码器。
smartdocsvectorcheck.slice保持CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
磁盘5MB/s和200IOPS；nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax600秒。
未编译、未并行重任务、未改原MySQL。[48条指纹前后核对](fingerprint-command.txt)
及[最终资源](postflight.txt)显示原47条基线不变、slice任务数0、mysql active，
memory.events仍high82471、max/oom/oom_kill均0。真实运行期间可用内存抽样1643656KiB，非峰值测量。

[单元终态](journal-and-status.txt)记录19:15:15退出1：这是CLI对后端failed的既有映射，
不是测试服务崩溃或OOM。未把退出码1隐藏成成功，也未因此自动重跑。

## 总体进度

30项最新状态为18 completed、1 failed、8 not_run、3 admission_rejected；人工业务判定全空。
所有历史共33次尝试、119请求/607214 token、96条引用，失败/复测均保留。
本次补齐A07拒绝分支的真实观察，不关闭整个T-13/M5/P0；仍需其余归档任务、
真实用户对具体方案的确认/执行、全部语义评审、部署和完整业务演示。
