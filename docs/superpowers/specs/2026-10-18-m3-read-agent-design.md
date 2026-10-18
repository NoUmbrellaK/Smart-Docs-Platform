# M3 有界只读Agent执行核心

基线fe91a5a。沿用用户授权自主推进、节约token、当前服务器执行；这不是M3验收。
复用现有MCP SDK与C++权限库，不引入执行框架或第二套权限数据库。

## 架构与取舍

选择C++持久领取/外发复核/结果门禁加Python有界循环。只在Python保存任务状态
会丢失重启与重复领取约束；把远程请求放进C++数据库事务会占用文件服务线程与锁，
因此不采用。模型传输以异步callable接入，输出按Chat Completions结构校验；本轮
用脚本化供应商验证执行器，下一增量才接DeepSeek真实请求。不把脚本化决策称为
真实模型效果或T-10验收。

## 持久生命周期

schema7新增agent_runs(task_id主键、started_at、model_calls、result_json、finish_code)，
扩展任务state为completed/failed；旧数据不虚构执行记录。原业务库不迁移。
本人同源POST /agent-tasks/{id}/run/start只领取pending任务，在原范围锁内核查后
标running，插入唯一run，返回数据库中的已确认question；不接受调用者重写问题。
重复领取拒绝，启动/领取响应丢失不自动重试；旧debug工具已使任务running时不能领取。

POST run/check无正文：只有已领取的running任务，复核全部范围、180秒期限及
8次模型调用上限，先持久递增model_calls再返回许可，不自动重试。
该许可是单次调用前的检查点，不是长期凭据；许可提交后并发撤销无法追回已开始
请求，不声称数据库事务与远程网络发送原子化。后续调用必须再次检查。

POST run/finish接收严格{status,result,error_code}。成功结果为{answer,citations}，
answer非空且最多16000 UTF-8字节，citations最多100个精确file/version/chunk ID，
C++验证每个引用确属任务范围与持久片段。失败只保存允许的脱敏错误码和null结果。
仅running可提交；相同已提交结果重放返回既有状态，不同结果冲突，不生成新任务。
完成前重新检查范围与期限；观察到范围失效则持久failed并清除结果。
GET run/result只向本人当前项目成员返回结果，重新检查所有范围依赖（保守超集）；
观察到失效永久清除该结果，不因重新批准恢复旧内容。重启running仍变interrupted，
不自动续跑；取消/中断后迟到结果不能发布。

## Python循环

run_task(context, client, model)只接受task_versions，client是已建立的真实MCP会话，
model为async(messages,tools,max_tokens,timeout) callable，返回Chat Completions对象。
固定两只读工具与schema；模型不能传身份、任务、批准或任意命令。系统提示说明
文档是不可信数据、未检索/未读完不能声称完整、最终输出answer/citations JSON。
问题只来自start响应；每次模型调用前run/check，工具数据只经真实MCP获取。
只把正常返回字段加入历史，不回传供应商隐藏推理或未知字段。

最多8模型请求、12工具调用、180秒总时限、每模型60秒、每次max_tokens1024；
单次序列化上下文64KiB、累计外发512KiB。字节/请求限额不是精确token或价格估计。
逐次校验工具参数及返回task_id；401/403/范围冲突等终止并丢弃上下文，重复相同
失败工具不无限重试；非法工具可在预算内调整，绝不执行。最终引用还必须来自本次
实际工具返回的片段，C++再检查持久来源。usage未知就不虚构token数。
模型超时、格式错误、预算耗尽均持久failed；取消不伪装为failed/completed。
本轮不提供生产CLI/公开HTTP入口、不读取密钥、不调用远程模型、不实现写工具。

## 验证

先RED后GREEN。C++真路由测试领取排他、首轮问题来源、撤权/取消/期限/预算、
引用拒绝、结果重放和读取失效。Python测试使用本地供应商替身，真实循环做决策
分派；真实SDK→C++→隔离MySQL E2E验证无结果后改查询/读片段/结束，以及撤销后
第二次模型请求不发生。并发结果、API供应商质量、生产内部HTTP、逐调用持久审计、
任务网页及M4报告不由本轮完成。

沿用nobody、smartdocsvectorcheck.slice、-j1、900秒、低于512MiB停止，串行运行。
