# M2 编码超时分层诊断

日期：2026-10-17；基线 `0a807d85f656732ffc95312133ff0f8acbec3102`。
本轮没有修改生产代码、模型、依赖、数据库结构或资源限额，也没有消耗 DeepSeek token。
目标是定位[前轮组合验收失败](../hybrid-browser/README.md)，不是补造通过结果。

## 已证实的原因与限制

当前每次编码请求都创建新子进程，在同一个 60 秒预算中重新校验模型全部文件、
导入依赖、初始化 ONNX 会话并推理。在原 5 MB/s、200 IOPS 和共享 memory.high
约束下，冷文件读取及加载耗尽了大部分预算；不是已证实的 CPU 算力不足，也没有
OOM。新鲜编码服务仍重现失败，排除了上一轮服务总寿命到期这一混杂因素。

第二轮完整边界记录见 [embedding-boundaries.jsonl](embedding-boundaries.jsonl)。
下表为从编码子进程诊断入口开始的累计墙钟时间，不是各阶段单独耗时：

| 观察点 | 首次请求 PID 648007 | 一次显式重试 PID 648074 |
| --- | --- | --- |
| 全部模型文件校验结束 | 48.490 秒，CPU 累计 0.897 秒，读取 213094400 bytes | 13.557 秒，CPU 累计 0.772 秒，读取 62132224 bytes |
| fastembed 导入结束 | 59.153 秒 | 15.689 秒 |
| 模型构造完成 | 截止前未观察到 | 47.734 秒，CPU 累计 2.733 秒，峰值 RSS 744208 KiB |
| 开始首个窗口推理 | 截止前未观察到 | 47.735 秒，截止前未观察到返回 |
| 调用方最终收到错误 | 请求后 60.183 秒 | 请求后 60.126 秒 |

首次请求到 service_enter 约 0.124 秒，到 child_wrapper_enter 约 0.191 秒。
两次父进程均在启动子进程约 60 秒后记录 `embedding_timeout`，随后服务退出并将
错误送到调用方；没有触及客户端 75 秒截止期。不能把这轮失败称为响应丢失、
鉴权失败或网关路由错误。

此前第一轮仅记录子进程阶段，重试在约 52.087 秒到达结果输出语句，但调用方仍
超时；由于当时没有父进程/退出边界时间点，不能确定该轮是在退出、回收还是其他
后续步骤超时，也不能据此宣称结果已成功返回。原始
[阶段记录](embedding-stages.jsonl)和[测试输出](profile-check.txt)保留，不把该局部
观察当作成功验收。第二轮只增强诊断，没有更改超时处理或自动重试策略。

第一轮模型加载期间还采样到进程 RSS 727460 KiB、状态 D、等待
`wait_on_page_bit_common`；共享组 anon=1090416640、file=136478720 bytes，I/O 和
memory pressure 均非零。结合上述读取量、低 CPU 时间及回收事件，支持“受限 I/O
与内存回收下重复冷加载”这一定位；尚不能量化各因素占比或据此要求物理扩容。
该单次只读采样见 [resource-observations.txt](resource-observations.txt)，由运行时命令
输出转录，未保存精确墙钟采样时间，不能当作整轮峰值或连续监测曲线。

## 复现与诊断边界

复用原 `hybrid_browser_test.py`，不删断言、不改真实模型结果。独立
[诊断入口](embedding_stage_probe.py)通过包装 `_run_child` 改用一个仅带跟踪的子入口，
最终仍执行原 embeddings.py 的 `_main`、模型哈希校验、输入/结果校验和资源限制。
记录内容只有 PID、阶段、代码行号、时间、CPU、RSS、缺页及读取字节；不记录正文、
向量、Cookie、worker token 或密码。包装器只供可信本机诊断，不是产品接口。

第二轮编码服务的启动入口替换为：

```text
python -B embedding_stage_probe.py service TRACE_PATH MODEL_DIR MODEL_PYTHON LOCK_PATH
```

它仍位于非 root、PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX 的
socket-activate 单元内；私有目录 0700、socket 0600，同一模型锁。客户端运行：

```text
scripts/test-with-mysql.sh -- python -B embedding_stage_probe.py client test/e2e/hybrid_browser_test.py TRACE_PATH
```

沿用原 PYTHONPATH、Chrome/ChromeDriver、编码 socket、输出目录配置和同一共享组：
MemoryHigh=1200MiB、MemoryMax=1600MiB、SwapMax=0、CPUQuota=100%、TasksMax=256，
/dev/vda 读写各 5MB/s、200IOPS、Nice=10。不可脱离这些约束在生产机直接运行。
每轮新鲜编码单元最长 900 秒，测试单元 780 秒；未提高已有 CPU/RAM/I/O 限额。
Python 跟踪本身有开销，因此这些数字只用于定位，不作为吞吐量或性能基准。

第一轮最初因沙箱拒绝 chmod 私有 socket 而在权限预检退出，尚未建库或编码；获准
收紧到 0600 后才启动实际诊断。没有放宽生产 socket 权限校验。

## 结果与下一步

- 第一轮 22:56:25–23:01:37：组合测试 0/1，225.327 秒，退出码 1。
- 第二轮 23:03:26–23:08:47：组合测试 0/1，232.685 秒，退出码 1，见
  [boundary-check.txt](boundary-check.txt)。schema 5 和 103 文件迁移夹具均通过。
- 本轮未重跑 Python/JS/C++ 全套，不能沿用前轮数字冒充本轮结果。
- memory.high 19412→27699；max/oom/oom_kill 均为 0。收尾无运行中的 smartdocs
  临时服务，编码服务显式停止、MainPID=0，生产 mysql 仍 active。

源文件绑定见 [source-sha256.txt](source-sha256.txt)，最终单元与限额见
[unit-status.txt](unit-status.txt)。诊断包装器语法检查、哈希核对与 diff 空白检查通过，
不将这些静态检查计入真实业务验收分母。

下一项有依据的实验是把受控模型初始化与单次请求执行分开，测量相同限额下是否
能保持有界、可终止的真实查询；优先复用既有模型与服务，不下载新模型或扩大主机
配额。必须保留模型完整性检查、禁网、全局单实例、请求截止期和失败不发布语义，
不能用预热到限额外、永久跳过校验或无限超时来制造通过结果。
此方向尚未实现/验证，不是部署建议；M2 仍未关闭，未进入 M3。
