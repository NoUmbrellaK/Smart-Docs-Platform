# M3 持久任务范围接口

这是M3任务范围基础；后续[有界执行核心](m3-read-agent.md)增加领取、模型前复核、
持久回答/失败终态。尚无真实模型接线、自动续跑、完整usage审计或Agent网页。
C++为身份、范围与取消状态的权威，Python不另建权限库。

## 创建和状态

共同前缀：`/api/v1/projects/{project_id}/agent-tasks`。全部接口要求有效会话Cookie、
当前项目至少reader角色且任务归当前用户所有。管理员不能借项目角色查看他人任务。
全部拒绝query；POST另要求同源Origin。

POST前缀，JSON最大min(32768,配置max_json_bytes)，示意：

```json
{
  "question": "检查所选公开模拟交接资料",
  "versions": [{"file_id": "32位小写十六进制ID", "version_id": "32位小写十六进制ID"}],
  "confirm_remote_ai": true
}
```

三个字段均必需且不能额外传user_id、task_id、approved等字段。问题非空，最大
4000 UTF-8字节（不是字符），允许换行/制表，拒绝其他控制字符；versions为1–100项
精确file_id/version_id对，同文件不能重复。confirm_remote_ai必须为JSON布尔true，
表示用户在受控AI入口确认问题和选中文件版本可用于远程AI，不是具体归档方案确认。
目前只有API，人工确认网页尚待接线；该普通业务接口不作为模型工具开放。

创建在事务中逐一检查版本为当前、available、文件未删除、策略remote_ai_allowed、
精确版本获批准；任何一项无效整批404 resource_not_found，不泄露名称。可以选择
尚未解析的文件。成功201，返回data：id、project_id、state=pending、versions、
created_at、updated_at、confirmed_at。ID由服务端生成。创建不幂等，客户端不可因
响应丢失自动重复创建；后续正式任务入口需单独处理这一交互，不把此接口当重试协议。

GET `/{task_id}`，无正文：返回上述持久状态和固定ID范围，不返回问题、文件名称或
片段，避免状态接口成为新的内容出口。项目成员被撤销后连状态也不可读。

POST `/{task_id}/cancel`，空正文：pending/running/interrupted变cancelled，多次
调用返回相同业务状态。取消不恢复任务、不清除已产生的业务结果。

## 任务绑定工具

- POST `/{task_id}/tools/search`：与[远程搜索](m3-remote-read-api.md)相同参数。
- POST `/{task_id}/tools/read`：必需file_id、version_id；可选chunk_id或range，互斥。
  range与[分页读取](m3-mcp-read-tools.md)同义；都省略仅返回处理状态/元数据。
  JSON最大min(4096,配置max_json_bytes)，拒绝额外字段、null range和空chunk_id。

先在同一事务锁定项目成员、本人任务和全部选中版本，再查询正文。搜索SQL在候选、
计数、向量扫描和最终返回阶段都带任务范围，范围外即使已批准也不能返回名称、
数量或片段。指定范围外版本返回404；未知或他人任务也返回404。

任一选中文件换新版、被删除、变为不可用或外发批准失效时，整个工具调用409
task_scope_changed，不悄悄缩小范围；恢复相同当前版本和批准后会重新按当前状态
核验。本基础层不持久记录失效事件；未来模型执行器必须在观察到范围冲突后终止
该次执行并清空/重建上下文，不能仅依赖下一次工具成功继续复用旧模型上下文。
因此这不是模型出口安全验收，也没有把旧project调试模式接到生产模型。

取消/中断后调用409 task_inactive。任务行锁让在途读取与取消串行：先获锁的
读取可先完成，取消提交后的新读取被拒绝；不能撤回已经返回或已外发的数据。

## 状态与容量

首个成功工具事务将pending变running；这表示工具工作已开始，不表示模型已运行。
工具事务失败则回滚，不虚报开始。C++启动将全部running转interrupted；pending
保持排队，cancelled保持取消；没有自动续跑。最多100个pending/running、同时
最多2个running，超限429 agent_busy。直接调试工具结束需主动取消释放名额；
正式领取的执行核心通过run/finish保存completed/failed终态，见运行API说明。

为当前小服务器保守串行任务读取事务（独立agent_queue_guard行）；不增加后台线程。
这不是并发吞吐承诺。进程/系统资源仍按部署与运维限额配置。

## schema 6 历史增量与当前升级

新增agent_tasks、agent_task_versions、agent_queue_guard，复合外键把任务/文件
限定同项目、版本限定所属文件。既有文件及索引无需回填，不凭空创建任务或确认。
此增量原要求schema6；当前执行核心已要求schema7。生产升级必须先停C++/worker/网关并备份，再运行既有
scripts/migrate.sh，确认schema_version=7后启动；本次只在隔离测试库验证，没有
升级原业务库。MySQL DDL不是整批事务，失败勿盲目重放半完成迁移，恢复备份后重试。
回退旧二进制也须恢复匹配的schema5备份，不能直接删schema版本记录。
