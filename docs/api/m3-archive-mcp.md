# M3 归档 MCP 接线

方案 A 第九个增量：`create_archive_plan` 与 `execute_archive_plan` 复用既有 C++ 后端。
无新依赖或迁移。可信本机调试器的 `task_versions` 范围可枚举五项工具；
`project_approved_versions` 仍仅两个只读工具。启动及0600配置见[原入口](m3-mcp-read-tools.md)。

## 参数与授权

创建严格接受 `{expected_revision,policy_revision,items}`，每项严格为
`{file_id,version_id,source_directory_id,target_directory_id,reason}`。
修订为0–4294967294整数，1–100项；ID为32位小写十六进制，目标可null表示待确认；
reason非空，C++限制1000 UTF-8字节。仅请求绑定任务 `PUT /archive-plan`，不确认、不移动。
具体版本、目标批准和冲突规则沿用[预览接口](m3-archive-preview.md)。

执行严格接受 `{plan_id,revision,operation_id}`，revision为1–4294967295整数。
operation_id必须来自网页对具体修订的真实确认，不能由模型自造；仅请求绑定任务
`POST /archive-plan/execute`。[C++执行接口](m3-archive-execution.md)负责最终权限和整批事务。
确认、拒绝、目录配置、结果查询不是额外MCP工具。

禁止额外身份、项目、任务、approved或URL字段。整份参数UTF-8 JSON最多32768字节；
100项是条数上限，不保证长理由的100项请求能装入字节预算。超限须缩短理由或显式缩小任务范围，
不得偷偷分批执行原本要求全成全败的方案。Python不复制权限表；会话、Origin及路径来自可信上下文。

## 返回与未知结果

沿用 `{request_id,task_id,duration_ms,status,data}`，data保留C++权威结果。
创建返回task_state=waiting_confirmation时，工具status=waiting_confirmation且isError=false：
仅表示方案已保存，不是文件已移动。显式重放已确认方案保留当前权威状态。
执行成功status=success，data含逐项结果、审计及操作ID。写工具无source_request_id，
不能冒充搜索/读取来源回执。两工具readOnlyHint=false、idempotentHint=true、openWorldHint=false；
执行另标destructiveHint=true。

400/413→invalid_request，401/403/404→access_denied，409→conflict，429→busy；
超时为timeout，传输失败/畸形JSON/其他HTTP状态为dependency_failed，不转发错误正文。
共享传输直连loopback、15秒socket超时、256KiB响应上限、每进程串行，无代理、重定向、缓存或自动重试。

写入超时、取消或响应丢失不能证明未提交。调用方经普通方案GET或执行/result查询，
不得自动更换操作ID、重新提案或声称已撤销。显式相同请求重放仍检查当前权限和修订。
已提交后的最小六字段回执可在文件外发撤销后供当前成员中的任务本人查询；
完整方案和执行重放仍受当前范围限制。回执不承诺文件现在的位置。

## 尚未接入的部分

现有Agent执行器采用显式白名单：普通问答只有搜索/读取，绑定交接清单时另有报告保存。
后续已接入[归档可信上下文和提案后退出等待](m3-archive-agent.md)；真实确认后的继续执行尚未接入执行器。
没有向模型暴露普通目录列表或未批准目录名称；五工具可调试不等于模型已能完成归档。

测试使用真实SDK/stdio、C++及隔离MySQL，不调用付费模型；见[证据](../evidence/m3/archive-mcp/README.md)。
M3/M4和完整P0仍未完成。
