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

本页修复后的真实S01结果待另行记录，不回写首次失败、冻结输入或原30项台账。
