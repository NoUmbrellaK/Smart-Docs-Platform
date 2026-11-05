# Q07：未找到安全审计报告时的搜索范围说明

2026-11-05，被测提交 `5044f672d956556e72b1a565ab61e9a0e5ddf27a`。
持续同范围授权下，仅装载冻结requirements@1/design@1人工资料运行一次；问题、原文、判据不变。
预设答案不发送模型。固定DeepSeek端点，最多6次请求×512输出，无自动重跑或归档确认。
本轮只保存评测证据，不修改产品、提示词或生产配置。

## 结果与限制

[权威结果](result.json)、[10条事件](trace.jsonl)、[审计汇总](audit-summary.json)一致：
首次模型响应提出两个独立搜索，同批metadata返回0项、keyword返回1项；下一响应保存回答。
这不是收到空结果后再决定改查询，不作为新的动态调整步骤证据。无独立正文读取调用；
Q07没有要求先调用读取工具，引用来自关键词检索命中的design@1片段。
唯一引用的file/version/chunk、行1–5、当前版本标记与固定原文SHA一致。

AI辅助检查：回答表示未找到安全审计报告，说明名称/关键词搜索、设计说明候选并非报告、
keyword_only及vector_state=pending等限制，明确不能据此断言项目内不存在该类文档。
但仍将所选版本范围统计称为“项目覆盖情况”，重复“未检索文件0”而未解释这是索引就绪计数。
C++搜索将统计限定在agent_task_versions，unsearched_files=total-indexed，
并不证明项目全量盘点、全部正文阅读或全部相关材料召回。这处措辞留待业务评审。
后端只留mode/query_bytes（6和18）；回答自述的查询词不是独立的请求原文证据。

仅metadata与keyword，无向量编码器、无hybrid请求，不统计为混合检索验收。
保留原回答、空白人工评审；completed仅表示任务结果保存，不代表业务通过或完整T-11验收。
不从1条定位正确推导人工引用准确率；完整业务判定仍为空。

## 用量与资源

2次请求分别1496输入+145输出=1641、2387输入+275输出=2662；
合计4303 token（3883输入+420输出），每次输出上限512，模型deepseek-flash。
缓存命中2304、未命中1579，金额未查询。
固定案例全部历史累计56次请求/264704 token；30项中8项模型执行完成、3项准入拒绝观测、
19项未执行；历史引用26条、人工已评0。保留历史失败，不更改冻结任务分母。

[命令](command.txt)、[37项预检指纹](preflight.txt)、[启动配置](launch.txt)、[终态日志](journal-and-status.txt)。
原服务器隔离MySQL/schema16、真实C++及stdio MCP；CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、Tasks256、读写5MB/s/200IOPS，nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、600秒上限。
21:43:51–21:45:42 Asia/Shanghai正常退出；slice TasksCurrent=0，原mysql保持active。
memory.high82471未增，max/oom/oom_kill均0；可用内存采样1884–2012MiB，非峰值测量。
10项迁移检查PASS不计固定业务任务分母；空密码初始化提示来自禁用网络的隔离MySQL。
包装器清理本次临时SQL和业务文件，可从冻结夹具再生；没有删除真实业务资料。
没有部署生产或重跑全产品回归。

私有快照/原回答/空白评审表：`/tmp/smartdocs-m5-q07-live.Qdj8HP/Q07`，
0700目录、0600文件、nobody所有，四份哈希见result.json。临时目录不是持久备份；
正式评审需受控保留原件。公开证据不复制原回答或源文件正文。
[离线核验](verification.txt)交叉核对轨迹、用量、引用、台账和指纹，不新增API请求。
下一项未执行问答为Q10（Q08/Q09已有准入拒绝观测）；M5和P0仍未完成。
