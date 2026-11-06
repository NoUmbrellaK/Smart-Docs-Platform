# S01 首次真实测试：问答输出截断

2026-11-06，被测及输入冻结提交 `7756a7e50ae3b59e27a059bc2e564af659a976b3`。
上轮为[S01独立清单](../supplemental-inputs-20261106/README.md)冻结问题和预期，本轮不改输入、提示或产品代码。
使用既有CLI/真实C++、MySQL、stdio MCP与DeepSeek；只选择获批合成deployment@2、rollback@1，
关键词索引、无编码器，不将结果称作混合检索验收。expected不发送给模型。

## 实际结果

[结果](result.json)、[审计统计](audit-summary.json)、[事件](trace.jsonl)：

- failed/model_invalid，采集完整；3次实际请求，8134输入+854输出=8988 token。
  缓存命中3968、未命中4166；费用金额未查询，cost_estimate=null。
- 首轮两次metadata搜索各返回1项；第二轮两次range读取各返回1片段，4次工具调用均success。
- 第三轮使用512输出token，恰好达到每次512上限；finish_reason=length、无工具调用，
  在tool_batch分支拒绝，任务失败。未保存答案、报告或归档方案，新增引用0条。
- 上限4次请求，实际第3次即因截断终止，第4次未发起；没有自动重试或重跑。
  未保存模型响应原文，不能推断被截断的答案已包含正确事实/引用或精确截断位置。
- 人工业务判定和引用准确率均未填写；不能用“读到资料”冒充任务完成。

## 原因与下一步

代码路径：agent_runner默认output_tokens=1024；deepseek传输层对无报告/归档终止工具的问答
执行`min(max_tokens,512)`。本次真实审计确认最终请求上限512及第三次响应length。
执行器只接受finish_reason=stop的完整答案，截断被拒绝符合既有安全规则。
不是内存不足、模型请求次数耗尽、正文读取失败或权限拒绝；不能据此证明提高上限一定成功。

[离线诊断命令](diagnostic-command.txt)与[日志](diagnostic.txt)：既有4项测试通过（0.767秒），
覆盖请求层预算/JSON模式和截断拒绝、不泄露响应原文；本地模拟供应商，0额外真实请求。
它们验证的是当前512行为，不是修复后的效果。本轮没有修改或放松产品/测试断言。
下一增量修正问答执行预算与传输层上限不一致，先做离线边界回归，再以相同冻结输入复测；
不改题、不丢弃首次失败，不以接受截断JSON或省略引用代替解决问题。

## 统计与资源

[补充台账](../supplemental-task-results.json)单独分母1，S01失败1次，3请求/8988 token/0引用。
原30项台账和原引用核查包不变：37次历史尝试/127请求/634573 token/96条引用，最新49条。
若合并调用成本观察，为38次尝试/130请求/643561 token；这不是31项业务通过率。
当前最新引用仍49条，50条样本门槛和人工语义核查均未完成，M5/P0未关闭。

[启动命令](command.txt)沿用用户重复同范围测试授权；最多4请求×512输出，固定DeepSeek端点，
凭据仅由systemd credential加载并在启动夹具子进程前移出环境，不写入仓库或输出。
共享slice串行CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写5MB/s和200IOPS，
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、600秒预算，原ECS2vCPU/3495MiB。
[终态](journal-and-status.txt)：实测退出1、进程已结束、TasksCurrent0，原mysql active，
memory.events仍high82471、max/oom/oom_kill0。最低已记录可用内存抽样1475740KiB，不是连续峰值。
[前置](preflight.txt)/[后置](postflight.txt)48条运行指纹一致。
私有原件 `/tmp/smartdocs-m5-s01-live.tYfpFa/S01`，0700目录/0600文件，nobody所有。
[采集](collect-command.txt)、[提取](inspect.py)与[复核](verify.py)只读原件，不调用模型；
公开结果只含合成任务数据、工具摘要、用量与原件哈希，无会话凭据。
独立只读审查核对上述失败/用量/预算路径与公开数据边界，未发现重要问题；不替代实测或业务评审。
