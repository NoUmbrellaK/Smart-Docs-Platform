# M2 有界混合检索后端

日期：2026-10-17。基线：`eb1cf8374812804122be6dad6982406bda6bd464`。
源码绑定见 [source-sha256.txt](source-sha256.txt)，接口边界见
[搜索 API](../../../api/m2-search-api.md#有界混合检索)。未新增数据库、依赖或远程模型调用。

## 验证范围

- Python 回归 **64/64**，其中新增 5 项查询适配测试：C++ 预检先于编码、
  编码失败重新鉴权、不返回陈旧预检结果、拒绝模型参数注入身份/向量/模式、
  编码期间权限撤销仍拒绝返回。
- 真实 C++ HTTP + 临时 MySQL + 固定本地 ONNX 模型闭环 **4/4**，
  **136.835 秒**（不含数据库初始化及 C++ 回归）。包含此前 3 项向量 worker
  回归，以及新增的实际查询编码→持久向量扫描→融合→引用流程：
  两篇人工英文资料分别描述数据库备份恢复和蛋糕配方，中文查询“如何恢复备份？”
  首位返回恢复文档，match_sources 为 vector，固定 version_id 引用正确。
  编码失败返回 keyword_only；撤销批准后不再返回该文件。
  这是小夹具功能验证，不是召回率/准确率评测，更不能推广成业务任务成功率。
- C++ 处理/搜索定向回归 **40/40**（0 失败、0 跳过），其中新增 6 项。
  新增用例覆盖两路融合去重、未批准资料及计数排除、真实查询向量契约、
  损坏索引降级、零索引、部分向量覆盖、MySQL 语句超时降级与硬错误保留、
  4104 个窗口超限拒绝扫描、200 候选上限/末页、撤销再批准的旧 attempt 隔离。
  已有 metadata/keyword、任务、发布及权限回归也包含在最终日志中。
- schema 5 初始化、迁移重放与 103 文件升级夹具通过。没有迁移业务数据库。

[完整最终输出](verified-check.txt)、[失败到通过摘录](red-green-excerpts.txt)及
[单元终态日志](unit-status.txt)已归档。最终单元 21:07:29 启动、21:19:16
`Deactivated successfully`，实际查询 Result=success、ExecMainStatus=0、MainPID=0；
命令及临时库清理正常完成。随后无运行中的 smartdocs 测试单元，生产 mysql 仍 active。
之前的 3/3、6/6 和本次 40/40 有重叠，不累加成更多独立测试。

## 复现与资源

在当前 ECS 的 `/tmp/smartdocs-safe.h4SzvX/repo` 非 root 测试副本执行，
与上述源哈希逐文件 cmp 一致。单元 `smartdocs-hybrid-verified`，
User=nobody、Group=nogroup、Nice=10、RuntimeMaxSec=1800。共享
`smartdocsvectorcheck.slice`：MemoryHigh=1200M、MemoryMax=1600M、SwapMax=0、
CPUQuota=100%、TasksMax=256，`/dev/vda` 读写分别 5 MB/s、200 IOPS。
延长测试总预算只为限 I/O 下串行收尾，不放宽 CPU/内存/I/O 上限。

编码服务 `smartdocs-hybrid-encoder` 使用既有缓存模型、同一资源组、
PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、MemoryMax=1200M；
socket 路径在 0700 私有目录内，实际模式 0600、属主 nobody。
监听创建是异步的：第一次 chmod 时路径尚未存在，随后针对已创建路径重新
chmod 并核验成功，才执行模型测试。模型测试结束后该临时服务正常停止。

核心命令（资源限制由 systemd 单元提供）：

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
PYTHONPATH=python SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-safe.h4SzvX/vector-socket/hybrid.sock \
  scripts/test-with-mysql.sh -- /bin/bash -c \
  '/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m2_vector_worker_test.py && stdbuf -oL bin/smartdocs_tests --filter=processing_'
```

先跑真实 worker，再跑会重置数据库的 C++ 用例，避免模拟任务残留污染领取队列。
临时 MySQL 沿用 performance_schema=OFF、32 MiB buffer pool；生产设置未改。
本轮观察到 memory.high 节流，计数由 2761 增至 6157；max/oom/oom_kill 仍为 0。
这些是资源组事件计数，不是模型峰值内存或性能承诺。

## 失败与修正记录

1. Python 查询适配层最初 4 项因组件尚未实现失败，随后实现并通过；第五项
   补充编码后的权限撤销回归。
2. C++ 首轮 0/3：旧接口拒绝 hybrid，两个夹具还暴露出默认 1 KiB 请求体
   限额过小。调整专用夹具和接口上限，首次绿色验证为混合 3/3、原搜索 6/6。
3. 审查后补充边界：缺少 vector_index_partial 的测试先失败；4104 窗口上限
   测试通过。原 SLEEP 超时夹具没有触发 MySQL 中断，不能当作超时证明。
   换成有界递归 CTE 交叉查询后，真实语句超时导致旧代码报 database operation
   failed；随后仅将 MySQL 3024 分类为 database_query_timeout 并在向量分支降级，
   断连等硬错误仍传播。复审未发现新的重要问题。
4. `smartdocs-hybrid-final` 曾采用反向联合测试顺序；发现队列污染风险后在
   临时库初始化阶段主动停止，未运行其业务用例，不将该单元计为成功。
   已核实进程退出和该临时目录清理；最终依据是重新串行执行的
   `smartdocs-hybrid-verified`，不是被停止的单元。

## 未覆盖/未交付

未重跑全部 C++/M1 浏览器或大规模性能验收，定向回归不能替代全套验收。
未安装生产常驻服务，未读取 DeepSeek key，未消耗付费模型 token。
Python 查询协调器是可信服务端可调用适配层，并非 HTTP 网关或 MCP 服务；
原工作台仍使用 metadata/keyword。混合检索界面、Agent/MCP 接入与 M2 阶段
综合验收仍未完成；整个 P0 目标继续保持未完成。
