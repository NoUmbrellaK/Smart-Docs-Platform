# M3 本人任务列表

2026-10-18，基线 c41d60faf0fc78d75dbbb739b27a07c3646ee8e3。
为后续任务网页补齐刷新后的记录发现入口；仅新增GET集合接口，不实现网页或自动执行。
复用现有权限、任务表与状态序列化，无新增依赖、迁移、后台进程或外部调用。
接口契约见[任务列表](../../../api/m3-agent-tasks.md)。

## 验证方法

先新增真实HTTP测试，旧二进制三个用例均因GET未注册返回405而失败，见[RED日志](red.txt)。
随后实施三个C++文件的最小修改，三个二进制串行增量[构建成功](build.txt)，未清理构建缓存。
[只读审查](review.md)无阻断发现，并补上跨时间组分页覆盖。

[GREEN日志](green.txt)：新HTTP测试 **3/3**（测试本体3.746秒），既有M3 C++回归
**17/17**、路由 **4/4**，0失败、0跳过，单元退出0；隔离库迁移和重放检查也通过。
不是全仓C++/Python全量复验。[哈希](sha256.txt)绑定实际被测源码、测试与三个二进制。
[最终状态](final-status.txt)：本轮单元MainPID均为0，GREEN及构建正常退出，RED是预期
失败。memory.events从high80272/max0/oom0/oom_kill0到相同值，无新增节流或OOM；
可用内存约1.65GiB，原MySQL仍PID975。夹具已清理本轮临时数据库和测试上传资料，
未删除业务数据；无残留隔离MySQL目录。初始化空密码警告仅属于禁网络临时测试库。

实际命令（由下述受限单元执行，不在宿主无约束运行）：

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
PYTHONPATH=python bash scripts/test-with-mysql.sh -- /bin/bash -c \
  '/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_task_list_test.py && stdbuf -oL bin/smartdocs_tests --filter=m3_ && bin/smartdocs_tests --filter=router_'
```

执行副本`/tmp/smartdocs-safe.h4SzvX/repo`，RED单元`smartdocs-m3-task-list-red`（180秒），
构建`smartdocs-m3-task-list-build`与GREEN单元`smartdocs-m3-task-list-green`（各900秒）。
串行运行，User=nobody、Group=nogroup、Nice=10、UMask=0077、NoNewPrivileges=yes、
TimeoutStopSec=5、KillMode=control-group，均归属`smartdocsvectorcheck.slice`：
MemoryHigh=1200MiB、MemoryMax=1600MiB、MemorySwapMax=0、CPUQuota=100%、TasksMax=256、
/dev/vda读写各5MB/s及200IOPS。主机可用内存低于512MiB停止；没有放宽限额。

新测试使用真实HTTP/上传/任务创建/取消/重启、真实C++及临时MySQL；仅测试夹具调整
任务创建时间、撤销成员。25条历史任务（23条同时间、2条更早）跨两页，第一页后再创建
新任务，第二页无遗漏/重复/插入漂移。还覆盖管理员不能列出他人任务、跨项目游标、
无会话与撤销成员拒绝、严格query及GET正文拒绝、批准撤销后只可见固定ID状态元数据。

## 范围限制

没有DeepSeek调用、密钥读取、模型推理或生产库迁移；没有运行完整仓库验收或性能评测。
数据库主列表查询有1000ms执行提示，但不等于整个请求截止期；未来历史量增长后需
评估复合索引。列表不提供总数、不返回问题/回答/文件名/正文、不领取或恢复任务。
创建仍非幂等，不能由列表推断丢失响应对应某次操作或自动重试创建。
只有两个只读MCP工具；任务网页、真实供应商验证、完整审计API和三个写工具仍待完成，
M3及P0尚未验收。
