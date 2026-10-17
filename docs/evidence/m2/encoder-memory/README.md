# M2 编码器内存池诊断与最小修正

2026-10-17；对照基线 `ced510803a48da47a8fbbf6ee899e95b52454329`。
本轮量化模型保留内存，没有运行浏览器或测试数据库；不替代此前失败的组合验收。

## 测量与修改

锁定的 fastembed 0.9.0 在 `common/onnx_model.py` 直接支持 `enable_cpu_mem_arena`。
两个新进程串行运行默认设置、关闭内存池；实际 ORT session options 分别为 true、false。
每组保留一个模型，编码同一短英文及 960 字中文人工文本，共 9 个窗口。
下表是进程 KiB，不是共享组总占用或 PSS；全事件见[默认组](default.jsonl)、
[关闭内存池组](no-arena.jsonl)。

| 阶段 | 默认 RSS | 关闭内存池 RSS |
| --- | ---: | ---: |
| 编码后 | 739624 | 653576 |
| gc.collect 后 | 739624 | 653576 |
| malloc_trim 后 | 739532 | 647544 |
| 重复编码后 | 741640 | 652820 |
| 再次 trim 后 | 739592 | 647608 |
| 历史峰值 VmHWM | 753800 | 662112 |

编码后 RSS 减少 86048 KiB（约 84 MiB）；默认组首次 trim 仅减少 92 KiB，不能靠
垃圾回收/trim 消除主要占用。两组内部重复结果严格相等，跨组完整结果 SHA-256
同为 `d9bc3c19fca7336bd245b9d7ffa742f1eb36647589cabdb75f7086f31f8e0226`。
默认组先跑，第二组受更热的文件缓存影响，**不能把 68.710 秒与 6.236 秒的首次
编码差值解释成开关带来的加速**。每组一次，不是长期或多尺寸负载基准。

生产代码只新增 `enable_cpu_mem_arena=False`，没有新增依赖、配置开关、常驻模型、
fork 服务或 ctypes/trim。模型哈希、依赖版本、CPU provider、窗口规则、结果校验、
索引修订和 60 秒子进程截止期不变；仍每批创建独立编码进程。

## 验证与复现

- 修改前编码器基线 8/8 通过。新增配置边界测试先以 `True is not False` 失败，见
  [red.txt](red.txt)；它只替换昂贵的 native 加载边界，不冒充真实模型。
- 修改后完整 Python 回归 **75/75**，12.644 秒、退出码 0，见 [green.txt](green.txt)。
- 修改后的真实 `encode_chunks` 创建新子进程，保留原 60 秒监督，人工样例校验
  **1/1**：9 窗口、完整输出与对照一致、2.821 秒、子进程峰值 RSS 662168 KiB，
  见[原始记录](real-child.jsonl)。这是热缓存条件，不保证任意冷启动耗时。
- 未重跑 C++/JS、HTTP/数据库/浏览器组合，不把本轮结果计作 M2 通过。

全部在当前 ECS 非 root、单模型锁、PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、
NoNewPrivileges=yes 下运行。共享组保持 MemoryHigh=1200MiB、MemoryMax=1600MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，/dev/vda 读写各 5MB/s、200IOPS、Nice=10。
两个测量进程各有 180 秒总寿命，不是延长生产请求时限；真实子进程校验单元最多
90 秒，内部仍为 60 秒。没有读取 DeepSeek env、调用远程 API 或改动生产数据库。

[测量脚本](encoder_memory_probe.py)仅作为历史材料归档。复现需用模型虚拟环境
`python -I -B`，四个参数依次为 **ced5108 基线** Python 目录、固定模型目录、
共享模型锁、`default` 或 `no-arena`，在上述限制内串行运行。若使用当前生产源码，
其默认已关闭内存池，不能再称为默认对照。
[真实子进程脚本](encoder_arena_smoke.py)使用修改后代码；含本机绝对路径，移植需调整。
[源哈希](source-sha256.txt)绑定修改后文件；对照基线 embeddings.py SHA 为
`97511a7a9f20af2cff598a5bc3737ffa722a3a2a04eb2dc6eb12ee791709510d`。

固定输入预先验证一次，两次 `_encode` 均执行完整模型 SHA、依赖、provider 和结果
验证；不是完整用户请求链。模型和结果一直被强引用，故不是理论最低内存。
trim 返回 1 不是释放字节数，Threads 快照也不是全程线程上限。探针无 fork、
socket、并发或故障注入，不验证 COW、常驻服务安全或浏览器/数据库共存性能；
磁盘 SHA 也不证明与缓存会话原子绑定。没有把实验包装器导入生产代码。

本轮 memory.high 35671→36274，max/oom/oom_kill 为 0。默认组正常退出自动回收，
其余单元保留退出状态后显式停止；见[终态与配额](unit-status.txt)。该修改降低了
已测样例的内存占用，尚未证明解决组合验收中的传输失败。M2 仍开放，下一步复验
真实浏览器链路，不据本轮结果要求物理扩容。
