# 报告事实表述候选复验（未通过，已撤回）

基线 `8fc67a25709689fe30ae32bae1546e6aa3c640f1`，2026-11-06。
本轮范围：语义预审 F01/F02。保持任务、资料、预期、预算、权限、保存回执和真正归档确认不变。

## 修改前失败证据与预先固定的观察标准

- [H07 原结果](../h07-live-20261106/result.json)成功保存报告，而
  [原文包](../citation-review-20261106/packet.json) H07-01 limitations 声称“未执行任何写入”。
  现有 REPORT_SYSTEM 已说保存会写草稿；不是提示未送达或代码未保存，而是模型未可靠遵守。
  将原来合并的禁止语句拆清：报告保存是写入；只能描述未改变源文件；没有回执不宣布保存成功。
- [H05 第三次](../h05-semantics-20261106/result.json)只有元数据读取，summary 却把三个不同名称称为命名冲突。
  共享提示明确区分相关候选、同目录同名冲突与有正文支持的事实冲突，不强行把所有冲突改为不确定。

这是模型输出质量问题。既有真实失败为 RED，不新增“提示含某字符串就通过”的伪行为测试，
也不增加逐词审查器替代语义判断。离线套件只证明执行边界回归；真实输出需与固定输入和原文人工比对。

测试前确定：为节省用量，先复验原 H05 一次，最多 6 请求，每次 4096 输出，失败不自动重跑。
只发送原获批人工资料的必要元数据、原问题、清单、提示和工具定义/结果到固定 DeepSeek 端点；
不发送冻结 expected、真实业务资料或用户会话，不确认或执行归档。
预先固定观察标准：仍只查元数据首页、没有正文读取、6 项 uncertain、0 正文引用；
报告把相关名称列为候选、不宣布已证实的重名/内容冲突、不声称没有任何写入或未获回执的保存成功。
本例是否改善不能代替 H07 原题复验，不能证明模型以后始终遵守，也不填人工业务验收。

## 离线回归与新增发现

[首次命令](offline-command.txt)/[完整失败日志](initial-offline.txt)：126 项运行 85.941 秒，
125 通过，`test_third_connection_is_busy_without_new_execution` 报 BrokenPipeError。
首轮采集命令因要求成功终态退出 1；随即查询同一 unit 确认 failed/exit1，才运行下一次，不因观察失败重启原进程。
`SearchHTTPServer.process_request` 在两个槽位占满时直接发 503 并 shutdown_request，未等待新请求体；
测试 `http.client` 分开发送请求头和正文，堆栈在正文 sendall 处报错，符合早关连接时序竞争。
该路径在本轮前后未改，不读取 SYSTEM/REPORT_SYSTEM。仍可能影响满载客户端，不能以“与提示无关”宣称无缺陷。
不捕获错误冒充 503、不放宽断言；在同一指纹和限额下仅完整复核一次，结果另见 offline.txt。
后续须独立重现并处理该 HTTP 满载路径，本轮不扩展到网络服务修改。

[指纹检查](fingerprints.py)确认原 48 个运行文件仅 agent_runner.py 改变，冻结题目/资料/预算均未改。
独立只读审查未发现本提示变更的安全边界问题，明确要求 H07 原题与稳定性仍不能提前宣称通过。

完整复核实际为 **126/126，85.232 秒**，未改任何代码或断言；首轮 125 通过/1 错误仍保留。
两轮均为 HTTP 本地接收器，禁外网；SDK 负向用例提示保留，不声称输出没有警告。
[采集命令](collect-offline-command.txt)/[终态](offline-status.txt)确认原失败单元和第二次单元均结束，
TasksCurrent=0，原 MySQL active，high82471、max/oom/oom_kill0。此复核不证明满载连接竞态已消失。

## H05 第四次真实结果

[启动命令](live-command.txt)，被测代码为 `8fc67a2` 加[候选补丁](candidate.patch)，
48 个文件的[前置](live-preflight.txt)/[后置](live-postflight.txt)指纹相同，仅执行器提示区别于上轮运行副本。
[结果](result.json)、[事件](trace.jsonl)、[审计摘要](audit-summary.json)：

- failed / scope_changed，2 次请求；7813 输入 + 316 输出 = **8129 tokens**，缓存命中2688/未命中5125。
  两次输出107/209，均未达到4096上限；不是已观察到的预算截断或 OOM。
- 一次 metadata 搜索成功，返回10项、0正文块；随后 `read_document_version` 的 `selector=status`
  被后端以 access_denied 拒绝，执行器结束任务。没有调用保存报告工具，没有报告、正文引用或归档。
- 这次多做了搜索首页之外的状态查询，未满足预先固定的“只查元数据首页后保存”路径。
  **不是成功读取正文**；无法评价报告是否改善 F01/F02，因为没有报告。
- 没有保留完整模型工具参数或供应商原始线包，脱敏轨迹不能区分错误 ID、参数选错或其他权限拒绝原因。
  `scope_changed` 是执行器终止码，不证明实际发生了权限/版本变化。单次失败亦不能证明由提示改动引起。
- 首次记录的失败和本次失败均不被改写成成功；未追加付费调用、自动重试、确认或执行归档。

候选未取得所需改进证据，因此仅用 apply_patch 撤回本轮自己的提示与对应接口说明修改；
产品回到 `8fc67a2` 内容，候选保留为可复现实验，不宣称 F01/F02 已修复。
在确认 live unit failed/exit1、TasksCurrent0 后执行[恢复命令](restore-command.txt)，
[恢复指纹](restored-fingerprints.txt)证明工作树与运行副本全部48项回到原基线，不改历史快照。
`fingerprints.py` 是测试期间候选指纹检查器，撤回后不应把它当当前产品检查器运行；最终使用 verify.py。

最新原30项台账为17 completed、2 failed、3 waiting_confirmation、5 not_run、3 admission_rejected；
H05保留4次尝试（12请求/71847 tokens），不选择第三次 completed 充当最新结果。
原集累计38次尝试/129请求/642702 tokens/96条历史引用；加独立S01为40次/135请求/660667 tokens，
历史98条、每任务最新51条引用。样本量不变并不代表新 H05 通过；人工业务/引用判定仍空，费用金额未查询。

[采集命令](collect-live-command.txt)/[提取器](inspect.py)只公开获批人工资料结果白名单，不公开密钥或会话；
原件位于 `/tmp/smartdocs-m5-report-wording.cUs1kn/H05`，文件0600/nobody、目录0700。
[终态](live-status.txt)确认测试进程全部退出、原 MySQL active，high仍82471、无新增OOM。
最低已记录可用内存抽样1508260KiB，不是连续峰值；沿用原2vCPU/3495MiB、CPU100%、
MemoryHigh1200MiB/Max1600MiB、Swap0、5MB/s/200IOPS、Tasks256的串行执行约束。
实际为 TXT 关键词索引，不声称混合检索验收。

## 最终核验与后续

在本工作树运行（须有权读取私有合成原件；不使用网络或密钥）：

```sh
python3 -B docs/evidence/m5/report-wording-20261106/verify.py > docs/evidence/m5/report-wording-20261106/verification.txt
git diff --check
```

[检查器](verify.py)/[结果](verification.txt)核对失败、用量、工具顺序、原件哈希、台账、撤回和资源终态；
这是证据一致性检查，不是模型效果通过。只读审查独立复算用量与台账未发现差异。
旧37次引用包和98条预审保持当时快照；其固定基线检查器应在对应历史提交使用，不改历史脚本来迎合新台账。
下一步优先确定性复现并处理本轮暴露的 HTTP 满载连接问题；随后补足状态查询拒绝的诊断证据，
再决定提示调整与 H07 复验。当前报告表述、人工评审、剩余固定任务及完整演示均未完成，M5/P0未验收。
