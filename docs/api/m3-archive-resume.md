# M3 确认后本机继续归档

方案 A 第十一个增量。复用既有 CLI、MCP、C++ 权限/事务及 schema15，
没有新增服务、依赖或迁移。提案仍由[归档 Agent](m3-archive-agent.md)动态搜索/读取后保存；
本入口只继续执行已经由真实用户确认的方案，不调用模型，不需要 API key。

## 显式入口

```bash
PYTHONPATH=python /path/to/mcp-venv/bin/python -B -m smartdocs.agent_cli \
  --context /private/task-context.json --audit /private/new-resume-trace.jsonl \
  --resume-archive
```

context 必须是本人持久任务的可信 `task_versions` 配置，权限/所有者规则沿用
[本机入口](m3-deepseek.md#本机可信入口)。不会读取密钥文件；此模式不构建模型适配器，
即使父进程存在 DEEPSEEK_API_KEY，也先移除，不传给 MCP 子进程。
每次显式调用都使用新的审计路径；拒绝覆盖已有审计文件。不要把会话或审计配置提交 Git。

先按[网页流程](m3-archive-execution.md#网页确认与恢复)查看具体修订并真实确认。
确认不自动启动本入口；现有网页直接执行按钮仍可独立使用。没有后台轮询、队列或自动恢复服务。

## 状态与写入边界

- 首先读取本人任务状态；`waiting_confirmation` 原样退出，不调用写工具。
- `waiting_execution` 时读取权威完整方案，核对任务/项目、confirmed、confirm、
  executable=true、无待定项及严格类型的 ID/修订。
- 只将该响应中的 `{plan_id,revision,operation_id}` 交给 `execute_archive_plan` 一次；
  不接受模型生成的操作ID，不在遇到新修订时自动切换到新确认。
- C++ 再次验证当前成员角色、固定范围、源/目标快照、真实确认和取消状态，整批原子提交。
  Python 的预检不能代替后端授权。
- 成功工具响应也必须经 `/archive-plan/result` 取得匹配三元组的严格六字段持久回执，
  才报告 completed；不凭工具文本推断成功。
- 已完成任务只读回执，不重放执行。文件批准撤销后仍按当前成员/任务本人权限读取最小回执；
  回执只证明历史提交，不保证文件当前位置。

单次控制器截止期90秒，普通请求上限15秒，工具调用上限30秒；不领取新运行槽、
不重置模型预算、不调用 start/check/finish/decision。MCP 初始化及进程退出沿用原入口机制，
90秒不是包含这些阶段的整进程 SLA。预览最多256KiB，回执最多64KiB。

## 不确定结果与错误

工具超时、响应丢失或畸形响应后只查询回执；不能确认则返回 unknown，不补写失败、
不自动重写或创建新操作ID。协程取消直接传播，后台已发出的 HTTP 仍可能提交；
下次显式运行先读持久状态/回执。不得把超时或退出码1理解为“文件肯定未移动”。

明确拒绝后可只读刷新任务状态；取消返回 cancelled，权限不足返回 access_denied，
方案快照/范围冲突返回 scope_changed。执行前的快照冲突不发送写工具。
待执行状态带错误时保留错误，不把它伪装成正常等待。操作者应重新核对方案/权限，
必要时显式修订并重新确认，再用新审计路径调用；本入口不替用户做这些决定。

CLI stdout 仅输出状态及白名单错误码，不输出名称、正文、完整方案或回执。
正常完成/正常等待退出0，运行错误/未知退出1，配置错误退出2。
审计新增 archive_resume，保留工具 attempt/result 和最终脱敏状态，没有 model_* 事件。
进程中途消失可能没有最终审计，持久业务结果仍以 C++ 为准。

## 验证与未完成项

[验证记录](../evidence/m3/archive-resume/README.md)包含真实 CLI/stdio/SDK/C++/临时 MySQL、
响应丢失恢复、重启、修订竞争、取消、改名和权限撤销。提案模型使用本地脚本替身，
不消耗 DeepSeek token；不代表真实模型分类质量或完整任务评测。
后续已接入[个人报告展示](m3-handover-report.md#个人报告网页)；网页任务模式选择、
生产调度和五工具整体验收仍待完成，M3/P0仍未验收。
