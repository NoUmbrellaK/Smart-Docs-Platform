# M2 编码器加载归因（未修改生产实现）

2026-10-18，源码基线 `66cd0db245ed73d1d46d63686efa66e272d6f5d3`。
本轮只运行三个串行、断网、非 root 的人工样例探针；不启动浏览器、临时数据库或远程 API。

## 结论

锁定的 FastEmbed 0.9.0 的 `OnnxModel.EXPOSED_SESSION_OPTIONS` 只有
`enable_cpu_mem_arena`，生产代码已关闭该选项。
[ORT 1.23.2 官方配置定义](https://github.com/microsoft/onnxruntime/blob/v1.23.2/include/onnxruntime/core/session/onnxruntime_session_options_config_keys.h)
支持 `session.disable_prepacking`；仅在实验进程拦截 SessionOptions 构造并设置 0/1，
实际 session 回读验证生效，未修改依赖安装文件。

| 同一人工样例，9 个窗口 | 预打包开启（当前行为） | 预打包关闭（拒绝采用） |
| --- | ---: | ---: |
| 首次编码后 RSS，KiB | 655104 | 664912 |
| 峰值 VmHWM，KiB | 662428 | 665176 |
| 首次输出 SHA-256 | d9bc3c19fca7336bd245b9d7ffa742f1eb36647589cabdb75f7086f31f8e0226 | 3868e57744ddb16be8424a17b145d71026803878a8beaad30d8171dbfc7d468d |

关闭预打包没有显示内存收益，输出也不再逐值一致；不能在原索引修订下直接采用。
这不证明其语义质量变差，本轮未评测数值偏差或检索质量。各组内部重复结果严格一致。
原始记录：[开启](prepacking-on.txt)、[关闭](prepacking-off.txt)。
两组首次编码分别约 44.5/3.9 秒，运行顺序和文件缓存不同，不能据此宣称加速。

第三个进程保持当前计算配置，仅在真实加载函数前后增加快照：

| 阶段 | RSS，KiB |
| --- | ---: |
| ONNX 会话加载前 | 77172 |
| ONNX 会话加载后 / 分词器加载前 | 410632 |
| 分词器加载后 | 645380 |
| 首次编码后 | 653652 |

会话加载期间净增约 326 MiB，分词器加载期间净增约 229 MiB。
这是同一进程的阶段 RSS 差，不是对象独占内存、PSS、稳定峰值或理论可回收量。
模型文件 235052644 字节，tokenizer.json 17083009 字节；磁盘大小不能代表运行内存。
[分阶段原始记录](loader-phases.txt)输出仍与开启组相同。下一步应优先研究分词器
加载/保留内存是否有不改变 token 序列的可行改进，不重复浏览器顺序实验。

## 安全范围与复现

三个探针均退出 0；这仅证明探针执行及内部重复校验成功，不是三个业务验收通过。
每次保留一个真实模型，两次调用现有 `_encode`，均执行模型哈希、依赖、provider、
窗口及结果校验。没有走正式请求的 60 秒父进程监督；探针总寿命 180 秒，
子进程自身仍执行现有 2 GiB 地址空间及 CPU 限制；未更改生产请求时限。

复现脚本：[原生选项探针](smartdocs-prepacking-probe.py)、
[观测包装器](smartdocs-loader-phases.py)。包装器仅借助 Python profile 钩子在首次
模型构造时安装观测函数，随后立即关闭 profile；不改变计算结果，耗时仍有观测扰动。
脚本含本机路径：包装器依赖 /tmp/smartdocs-prepacking-probe.py。参数及模型解释器
见原始日志首行；模式 0/1 分别为开启/关闭，包装器只接受 0。只用于历史诊断，不安装生产服务。

共享 slice 保持 MemoryHigh=1200MiB、MemoryMax=1600MiB、SwapMax=0、CPUQuota=100%、
TasksMax=256，磁盘读写各 5 MB/s、200 IOPS；单元 User=nobody、Group=nogroup、
Nice=10、PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes、
UMask=0077、RuntimeMaxSec=180、TimeoutStopSec=5、KillMode=control-group、
RemainAfterExit=yes。必须先核对限制再串行运行，不在无限额主机进程中加载模型。
探针确认 IPv4/IPv6 socket 创建失败；真实计算仅使用预设公开人工文本。

源码与执行副本 embeddings.py 已 cmp 一致，SHA-256：
`7c0913f6c470ef5006b5a64e99ef5167b2ccc2f5fbf067b44f512f9880ded781`。
未改应用、测试或模型文件，未重跑应用回归和组合验收。memory.high 全程前后均为
47501，max/oom/oom_kill 均为 0；[终态](final-status.txt)确认无运行中的临时服务，
原 MySQL active。M2 仍未验收，本轮结果不构成物理扩容必要性结论。
