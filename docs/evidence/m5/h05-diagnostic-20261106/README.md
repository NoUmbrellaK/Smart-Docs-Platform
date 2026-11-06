# H05 当前版本受控诊断复测

预先记录：基线 `5baa273f17a8f5c9512370ed743d5b4ea26eb119`，2026-11-06，第五次尝试。
仅运行原冻结H05一次，最多6请求、每次4096输出token，不自动重跑，不确认/执行归档。
继续使用获批11份人工资料的必要元数据、原问题、清单和工具定义/结果；不发送expected或用户会话。
候选提示已撤回，本次使用当前已提交提示，因此不是第四次候选的单变量重放。

问题：当前代码是否仍发生状态读取拒绝；若有读取，使用selector及此前是否见过文件/版本组合的
脱敏诊断缩小原因范围。诊断不能证明历史失败根因；本次不根据结果补改原题或放宽权限。
业务观察仍按原题：首页元数据、无正文、六项uncertain、0正文引用，明确分页和核查限制。
另外检查报告是否仍无依据断言命名冲突或声称没有任何写入；不把运行完成当语义通过。

运行命令见 [live-command.sh](live-command.sh)；独立私有产物目录
`/tmp/smartdocs-m5-h05-diagnostic.luGADy/H05`。沿用当前服务器受限slice，串行执行，
不停止生产MySQL，不修改生产数据或服务配置。

## 实际结果：失败，未追加付费复测

[结果](result.json)、[原脱敏事件](trace.jsonl)、[用量汇总](audit-summary.json)：

- failed/repeated_tool_error；4次请求，22813输入 + 5825输出 = **28638 tokens**。
  输出依次100/1939/1893/1893，均低于4096；不是已观察到的输出截断。
- metadata搜索1次，返回10项、0正文块；没有调用read_document_version，因而没有read_targets。
  此次未触发第四次的状态读取拒绝，不能证明该问题已消失，也不能补推其错误目标。
- 随后保存报告3次均被后端拒绝为invalid_request；第3次触发现有重复失败保护。
  无报告、无引用、无归档；测试入口没有重跑任务。运行中的失败反馈与后续工具尝试，与
  “整任务不自动重跑”不同，3次保存尝试全部计入记录，没有隐藏或扣除。
- 当前未保存报告正文，不能评价六项状态、报告表述或业务语义是否改善。
  本次代码使用已撤回候选之前的提示，且新夹具ID会改变第一页成员，不做单变量因果归因。

## 离线定位与证据边界

实际调用已经越过Python schema检查并到达C++，三次都记录item_count=6。
`code/agent/handover_reports.cpp::ValidateReport` 仍可能因清单key不匹配/重复、
状态与引用组合、空白/控制字符或UTF-8字节超限拒绝；引用来源错误在C++另有invalid_source错误码，
但也被当前HTTP/MCP审计归一为invalid_request，不能仅凭该状态排除。
`mcp_read.backend_read` 将400/413归一为invalid_request，现有后端审计仅保留item_count，
没有原始参数或细分拒绝原因。故**不能断言具体是哪一字段导致本次失败**。
工具描述已说明字节限额，但JSON schema的maxLength按字符计算；这是可定位的契约差异，
不是已证实的本次根因。本轮没有放宽校验、截断报告或改动提示来猜测修复。

下一增量应先在C++权威校验处确定最小、不含正文/ID的拒绝分类及离线用例，
使后续受控实测能区分原因；不要再原样盲跑H05，也不把新增诊断本身当作业务修复。

## 复核、资源及台账

[采集命令](collect-command.sh)在同一unit已failed/exit1、slice TasksCurrent=0后执行。
[前](preflight.txt)/[后](postflight.txt)48项指纹相同，运行副本和工作树逐字节一致；
相对query-budget基线只含已提交HTTP收尾、读取诊断和关联测试变化，题目/资料/预算未变。
Python3.10.12/MCP1.30.0/httpx0.28.1；沿用2vCPU/3495MiB ECS，CPU100%，
MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，磁盘读写各5MB/s、200IOPS。
收尾只读确认的slice配置见 [resource-limits.txt](resource-limits.txt)。
[终态](live-status.txt)high82471、max/oom/oom_kill0，与前基线一致，无新增节流/OOM；
最低已记录可用内存抽样1473076KiB，不是连续监测峰值。原MySQL仍active，临时测试库已由入口清理。
实际TXT关键词索引，无向量编码，不声称混合检索或生产部署验收。

H05累计5次/16请求/100485 tokens，保留所有旧尝试；原30项最新仍为17 completed、2 failed、
3 waiting_confirmation、5 not_run、3 admission_rejected。原集历史39次/133请求/671340 tokens；
含S01共41次/139请求/689305 tokens，历史98/最新51条引用不变。人工业务/引用判定仍空，费用金额未知。
[verify.py](verify.py)只验证证据和台账一致性，不判定业务通过；M5/P0未完成。
汇总检查器首次未兼容两份旧H01结果的顶层citations_total字段，产生KeyError，
失败记录见 [initial-verification.txt](initial-verification.txt)；按实际旧格式读取后重新核对，
没有改写旧结果或填默认零。最终输出见 [verification.txt](verification.txt)。
独立只读审查复算用量、历史/最新分母及隐私边界，未发现阻断项；按审查收紧参数调整的措辞，
不因未保留原始参数而声称知道其具体变化。审查不代替真实业务或语义验收。

复核命令（需要读取受保护的人工测试原件，不使用密钥/网络）：

```sh
python3 -B docs/evidence/m5/h05-diagnostic-20261106/verify.py
git diff --check
```
