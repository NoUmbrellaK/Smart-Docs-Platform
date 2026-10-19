# 方案A：个人交接报告后端

基线67270ff；依据A-03/R-04/T-01/T-08/T-16/T-17/T-18/T-19。
延续已批准的方案A与自主执行授权，新增一个报告后端闭环，不扩展P1。

## 取舍与协议

每个绑定清单的任务最多生成一份不可变个人草稿，version=1；修改/重新核查需新任务。
不做可变报告编辑器或共享报告，以服务端操作ID和唯一约束处理重放。
schema14：agent_runs增加report_operation_id NULL/唯一；仅新版start为绑定清单任务生成并返回该ID。
旧run不补造ID。handover_reports持久id、task_id UNIQUE、operation_id UNIQUE、request_json、
content_json、sources_json、checklist_json、created_at；关联task和run，不另建权限系统。

PUT /api/v1/projects/{project}/agent-tasks/{task}/handover-report，同源会话、Reader本人任务、最多65536字节。
严格body={operation_id,report}；report={summary,limitations,recommendations,items}。
三项文本分别最多8000/4000/4000 UTF8字节且非空；每个清单key恰好一项，
item={key,status,notes,citations}，status为found/missing/uncertain，notes非空<=2000字节。
found必须有引用，missing不得有引用，uncertain可有；每项最多10，整份最多100引用，项内不重复。
引用精确{file_id,version_id,chunk_id}，必须在该任务已准入来源回执的citations中并仍存在且版本匹配。
文字是资料，不解析授权/指令；字段不能加入用户身份、依赖全集或批准状态。

首次保存需running、绑定清单、model_calls>0、180秒内、服务端操作ID一致，整个任务范围仍有效。
来源由C++合并所有model_call非NULL回执，含未显式引用版本；不从模型引用反推，不补造旧历史。
清单来自任务快照；文件名、页码/行号、片段原文、受保护来源URL由服务端补充。
报告、审计handover.report.save、task completed同一事务；失败全部回滚。
保存成功仅返回{id,version:1,url,markdown_url}，不向模型回传整份报告或新资料。
已经保存后同operation_id+同report请求重放返回既有回执（包括重启/超时之后），仍重验权限与依赖；
不同内容/ID409，不能重复审计或覆盖原报告。取消/失败/中断不得开始新的保存。

## 读取与下载

GET同路径返回私有草稿：项目/时间/任务、选择范围、清单版本、报告项目/建议/限制、
固定引用与支撑片段、实际来源版本。GET同路径/markdown生成attachment text/markdown。
两者及run/result均重验本人、当前成员、清单修订/批准、全部实际来源版本仍当前/可用/未删除/获批。
读取不把未送入模型的选择项冒充实际依赖；未引用但已准入资料失效必须阻止全文访问。
引用片段仍需存在。过期409不返回旧正文；不静默换版本。取消/状态仅元数据，不作内容缓存出口。
run/result返回kind=handover_report和报告回执，result=null；同样重验依赖，不把草稿伪称完成文件操作。
响应no-store；Markdown转义HTML/Markdown文本，不允许模型提供可执行HTML或任意URL。
来源URL指向现有鉴权固定版本chunk接口，显示物理页/行号；不是公网静态资料链接。

## 边界与验证

格式/引用归属验证不证明原文支持结论；报告明确个人草稿、语义未验收，limitations必须填写。
目前不复用跨任务会话；来源台账保守表示获准发送，不证明供应商收件。
不新增MCP写工具/网页/生产入口或调用真实模型，本后端随后由save_handover_report使用。
测试真实HTTP/SQL：Reader可保存、其他用户不可读、精确/并发/丢失响应重放、回滚、重启；
未准入/伪造引用拒绝，未引用依赖撤销/新版本/软删/成员变化/清单变化后save/read/download拒绝；
空证据uncertain可保存且不伪造出处；Markdown安全与长度；截止/取消后不新增写入。
复用当前ECS与隔离工作树，重负载串行make -j1，既有CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、5MB/s/200IOPS限额，MemAvailable<512MiB停止自有单元。只迁移临时库，不改生产库。
