# 问答预算对齐与 S01 复验

2026-11-06，修复基线 `47a6b5aa8ba5557ec9d55890608731711e205909`。
[S01首次实测](../s01-live-20261106/README.md)3请求/8988 token，最终512输出token、finish_reason=length，
严格拒绝后无答案/引用。执行器默认请求1024，DeepSeek传输层又压到512。

## 最小修正

只改deepseek.py一行：普通问答 `min(max_tokens,512)` 改为 `min(max_tokens,1024)`。
调用方较小额度不提高，非法类型/0/负数/超过8192仍在发包前拒绝；报告、归档终止工具的按任务额度不变。
不改变JSON模式、length拒绝、来源校验、权限、真实确认、次数/时间/字节门禁，不增加自动重试或新配置。
S01题目、两份资料和预期不改；提高上限不保证模型语义正确，也不自动构成业务通过。

## 离线证据

[RED命令](red-command.txt)/[日志](red.txt)：13项、6个失败（含子用例），全部为512!=1024。
新增测试验证实际HTTP请求体和审计额度：1/128/512保持，1024使用1024，1280/8192问答仍限制1024；
非法额度不得发请求。原有JSON/报告/归档预算测试一起保留。
[首次完整命令](green-command.txt)/[日志](initial-full.txt)：126项，仅多步集成测试旧512预期失败，
其余125项通过；保留失败，不将它算成完整通过。随后仅将该预期改为1024，保留其他断言。
[最终命令](final-command.txt)重新执行完整MCP/Agent套件，外部供应商均为本地接收器，禁止外网。
[最终日志](final.txt)：126/126通过，86.699秒；SDK在负向工具用例中的未列出工具提示保留。
[采集命令](collect-offline-command.txt)/[终态](offline-status.txt)：退出0、TasksCurrent0、原mysql active，
memory.events仍high82471、max/oom/oom_kill0。[后置指纹](postflight.txt)与前置一致。

沿用原ECS共享slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写5MB/s与200IOPS；
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMax600，串行运行。
[运行指纹](preflight.txt)核对48条，工作树与运行副本一致；相对S01首次实测仅适配器与两个测试文件改变。
独立只读审查确认上述测试预期遗漏，未发现其他安全边界改动；旧README中512说明是历史增量实际额度。

## 原 S01 第二次真实测试

被测修复提交 `0df877ef58ae4b4eb557e2df31b19db0d1c69b0c`，冻结清单仍来自7756a7e。
[启动命令](live-command.txt)沿用用户同范围重复测试授权，最多4请求×1024输出，固定DeepSeek端点；
仅获批的人工deployment@2/rollback@1、原问题/系统提示/工具定义与结果，不发送expected或业务资料。
凭据使用systemd credential，不打印或提交；CLI在启动夹具子进程前移除环境中的key。

[结果全文及原文](result.json)、[审计摘要](audit-summary.json)、[事件](trace.jsonl)：

- completed，3次请求，8122输入+855输出=8977 token；缓存命中5120、未命中3002，费用金额未查询。
- 两次metadata搜索→两次range读取→保存问答，4次工具均success；无报告/归档工具或写操作确认。
- 三次输出分别110、224、521 token；最终521超过原512上限，但未到新1024上限，本次无截断/拒绝。
  全部回答及两条引用成功通过现有严格校验，不是通过容忍不完整JSON实现完成。
- 答案列出备份ORBIT-27、哈希校验、业务服务→解析进程顺序，以及回滚停写、同备份点恢复、
  核对哈希/权限/引用后开放入口；明确两份资料未说明回滚触发条件，结论限于这两份文档。
  本轮只作内容观察，不填写人工业务通过。未保存原始供应商请求/响应线包，不推断模型内部过程。
- 两条引用分别对应deployment@2、rollback@1，均当前单块全文，行1–5；
  [复核](verify.py)/[输出](verification.txt)逐条与冻结原文、文件/版本/片段ID、SHA和行号核对。
  自动定位检查不等于人工语义正确，business_verdict/citation_accuracy仍null。

本次支持保留有界预算修复，但新夹具ID/模型输出可能变化；不声称两轮文字完全一致、统计稳定或所有任务改善。
原S01失败不删除，两轮合计6请求/17965 token、执行完成1/2；不是业务通过率。
[独立补充台账](../supplemental-task-results.json)分母仍1，原30项台账不变。
最新引用49+2=51，历史96+2=98；原核查包保持历史快照，本次两条原文与空白评审列在result.json。
合并调用观察为39次尝试/133请求/652538 token，人工引用检查、业务任务集及完整演示仍待完成，M5/P0未验收。

[采集命令](collect-live-command.txt)/[提取器](inspect.py)复用既有采集及评审，不追加模型调用。
[前置](live-preflight.txt)/[后置](live-postflight.txt)48条运行指纹完全一致。
[终态](live-status.txt)：退出0、TasksCurrent0，原mysql active，high仍82471、max/oom/oom_kill0。
最低已记录可用内存抽样1519228KiB，不是连续峰值。原ECS2vCPU/3495MiB、Python3.10.12、
MCP1.30.0、httpx0.28.1、关键词索引且无编码器，不冒称混合检索验收。
私有原件 `/tmp/smartdocs-m5-s01-budget.sKbXrz/S01`，目录0700/文件0600、nobody所有；未开启人工归档审核现场。
复验结果再经独立只读审查，引用/哈希、两轮及合并统计、公开数据边界未发现重要问题；不代替人工语义验收。
