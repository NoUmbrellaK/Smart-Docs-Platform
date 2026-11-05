# H03：部署资料尚未解析的首次真实执行

2026-11-05；代码 `e6bd745cc5334d9b895b76a5a88ff5b2ff9a96dc`。
本轮无产品或冻结输入修改，[37项运行指纹](preflight.txt)与工作树一致。
按同范围持续测试授权使用6份获准人工资料及标准6项清单，最多6请求、每请求4096输出token。
deployment@2按既有夹具保持parse/keyword pending、无片段；其他5份完成解析。
不发送期望答案、真实业务资料或用户会话，不代替真实归档确认。

## 观察与限制

[结果](result.json)、[审计](audit-summary.json)、[26条轨迹](trace.jsonl)及后端状态一致：

- 第1轮空查询metadata列举6个文件。
- 第2轮4次range读取，各返回1片段。
- 第3轮1次range读取返回1片段，另1次status读取返回processing/0片段。
- 第4轮单独save_handover_report，保存个人draft版本1、清单修订1；成功后不再请求模型。

工具批次1/4/2/1，8次派发，无模型或工具拒绝；processing是未就绪状态，不冒充正文读取成功。
六项报告状态与冻结预期一致：requirements/design/api/test/issues为found，deploy为uncertain。
部署项说明文件存在但正文不可用，不能当作已核查或断言文件缺失，无部署引用；建议解析后再核查。
其余5条引用绑定当前requirements@1/design@1/api@2/tests@1/issues@1，固定ID、版本、chunk、
原文SHA及行号核对一致。前4份行1–5、issues行1–3，字符数93/86/111/69/34，均单分块。
报告的6个source_versions包含未被引用的deployment@2，未把依赖缩成只有5个引用版本。
样例测试报告的42项、40通过、2失败如实保留；这些是人工资料中的事实，不是平台本轮测试统计。

摘要和限制明确任务范围、正文短小、部署未就绪、语义检索不可用，不声称全部齐全或业务验收通过。
本轮只有metadata和精确读取，没有keyword/hybrid请求，不计混合检索验收。
摘要仍有“本任务范围内（当前目录6个文件）”措辞，虽后文明确不代表项目或组织其他文件，
也不能据此证明整个目录覆盖；该表述留待人工评审，不宣布所有范围问题已解决。

工具日志未保存原始status响应或调用的文件ID；“char_count=0”等细节是报告自述，
夹具pending状态、报告引用的版本ID和processing/0片段轨迹相互对应，但不是独立原始报文证据。
无原始供应商请求捕获或全库前后快照，不以此关闭完整T-09/T-11/T-18。
未触发归档或共享；保存草稿是写操作。全部文本检查为AI辅助，人工业务和引用判定仍为空。

## 用量、资源与保留

deepseek-flash，4请求输入/输出分别2625/80、3594/462、6364/201、7372/1711；
22409=19955输入+2454输出，缓存命中13440/未命中6515，金额未查询。
每次输出上限4096沿用6项清单的现有规则；无自动重跑或额外付费诊断。
[命令](command.txt)、[启动](launch.txt)、[终态](journal-and-status.txt)。
当前ECS隔离MySQL/schema16、真实C++/stdio MCP，无向量编码器、无生产部署。
CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写5MB/s/200IOPS，
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、600秒上限。
22:19:50–22:21:51 Asia/Shanghai正常结束；最终slice TasksCurrent=0、原mysql active。
可用内存采样1810–1966MiB，非峰值统计；memory.high82471未增，max/oom/oom_kill为0。
10项迁移检查PASS不计业务分母；空密码警告属于禁网络的临时MySQL。
包装器清理本次临时SQL及业务文件，可由冻结夹具重建，未删除真实资料；未重跑全产品回归。

私有原件及空白评审表：`/tmp/smartdocs-m5-h03-live.Lhagkg/H03`，0700目录/0600文件/nobody所有。
四份原件哈希见result.json；公开证据不复制原文、报告全文或访问凭据。
临时目录不是持久备份，正式评审前需受控保留。
[离线核验](verification.txt)及[命令](verification-command.txt)不调用API。

固定30项：11项模型执行完成、3项准入观测、16项未执行；
20次历史模型尝试累计70请求/324011 token，38条历史引用，人工判定仍为空。
保留所有失败历史、不扩充分母、不把执行完成当作业务通过。M5及P0尚未完成。
