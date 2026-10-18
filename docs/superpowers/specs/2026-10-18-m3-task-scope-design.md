# M3 持久任务与固定版本范围

基线95e452c。用户已授权继续M3、小问题自主决定、正常推送；复用既有MySQL/C++/MCP，
不引入框架或在线迁移。本轮新增子系统，先写清边界再测试实现，不把局部完成当成M3验收。

## 选择与目标

选择C++持久任务范围，不采用Python事后过滤或仅配置文件声明允许范围：过滤前
名称/计数已泄露；本机配置不能证明有真实任务或当前取消状态。完整模型执行循环
依赖这个边界，但不在本轮同时实现。

用户会话POST /api/v1/projects/{project_id}/agent-tasks创建任务，精确字段为
question（非空，最多4000 UTF-8字节）、versions（1–100项file_id/version_id对，
同文件不得重复）、confirm_remote_ai（必须为JSON布尔true）。身份来自Cookie，
POST同源；此操作表示用户确认问题和版本范围，不是归档计划确认。模型工具没有
创建任务、修改范围或确认接口。创建锁定成员及文件/版本，要求指定版本当时为
当前、available、文件允许外发且该版本获批；不要求已解析。混入非法项整批拒绝。

服务端生成task_id；owner/project/question/确认时间/固定范围持久保存。GET
/agent-tasks/{task_id}仅任务本人且仍有项目reader权限可查看；返回状态、固定ID
范围、创建/更新时间，不返回问题或文件名称，避免状态查询成为新内容出口。
POST同路径/cancel只需空正文，同源检查，幂等取消pending/running/interrupted；
不能借取消恢复任务。创建不是幂等接口，客户端不得自动重试创建。

## 任务读取边界

任务工具使用POST /agent-tasks/{task_id}/tools/search（原搜索JSON）和/tools/read
（file_id/version_id以及可选chunk_id或range，互斥）。服务端注入task_id，模型
参数不含任务、用户、项目或批准。沿用原ProcessingService的检索及固定来源构建，
不复制权限规则。普通及旧remote-ai调试接口保持兼容，不升级为生产Agent入口。

同一事务先检查项目成员，锁队列守卫、本人任务、按file_id排序的任务范围文件
及精确版本；检查全部范围仍为当前/available/获批/未删除。任一失效返回409
task_scope_changed且不返回工具数据；未知/他人任务和范围外读取统一404。
搜索SQL在候选、计数、向量以及最终返回检查中均加入任务版本范围，不能先查
全项目再过滤。任务范围不可修改；新版本不被自动加入，旧任务不继续使用旧上下文。
取消和读取以任务行锁串行：已获锁的读取可先完成，取消提交后的新读取必拒绝，
不承诺撤回已返回数据。真正模型出口仍需下一增量再次检查所有上下文依赖。

## 状态、资源与迁移

新任务pending；首次成功任务工具读取转running；取消为cancelled；C++启动将
running转interrupted且不自动续跑。pending也可取消。首版最多100个pending/running
任务、同时最多2个running；创建与首次读取使用独立守卫行，超限429。单机初版
守卫也串行任务读取事务，不在此引入租约/心跳/多Worker。任务完成/失败、模型配置、
调用日志和完整时间/token预算由后续执行器补齐，不能把本轮任务状态当模型运行证明。

schema6增加agent_tasks、agent_task_versions、agent_queue_guard；字段外键、版本
对与唯一键在数据库约束，旧表无数据改写。C++要求schema6，升级需先停服务和备份；
DDL不具备整批原子回滚，只在隔离库验证，失败需恢复备份而非盲目重跑半完成DDL。

MCP配置新增scope=task_versions，task_id必须使用创建接口返回值；每次请求固定
走任务工具路由。旧project_approved_versions保留为明确的本机只读调试模式，
不能被未来生产模型入口接受。两种模式都不允许模型覆盖配置字段。

## 验证

测试先行：创建真实任务失败于缺失路由，再验证确认/非法版本整批拒绝、所有者隔离、
范围外名称/计数/正文排除、批准/新版/删除/成员变化、取消幂等与重启中断、容量。
SDK协议测试确认task_versions只走任务路由；真实stdio→C++→MySQL证明持久身份和
取消后拒绝。schema5→6重放不损坏已有103文件夹具；全仓C++用例分组复验。
沿用nobody、smartdocsvectorcheck.slice、-j1、900秒单元，禁止并行重载/模型调用；
MemAvailable<512MiB停止测试。无DeepSeek调用、无新依赖、无线上部署。
