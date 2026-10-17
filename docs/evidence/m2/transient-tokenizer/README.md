# M2 推理常驻、分词短生命周期原型

2026-10-18，北京时间；基线 `8a6e516d75ebe5d698f3ff1a2058af7f330a32d4`。
按[堆内存归因](../resident-heap/README.md)验证一个可丢弃原型，不改正式入口或索引修订。
原浏览器组合仍未通过，M2 不关闭。

## 被验证的机制

1. 使用现有 `verified_snapshot` 对全部五个文件做完整 SHA 校验和 memfd 封印，
   全部通过后才加载唯一 ONNX 会话；仍关闭 CPU arena，禁用 provider fallback。
2. 关闭权重快照，保留 tokenizer 和 special-tokens 两个只读封存快照，共
   17083973 bytes。保留的是原始字节，不是约 279 MiB 的原生分词器对象。
3. 每请求 exec 一个新的 Python 分词进程，只继承这两个 fd，构造原分词器，复用
   `_prepare_chunks`。在 child 的 `/proc/self/fd` 中读取封存字节，并复核封印。
   输入仍 64 KiB、准备结果 256 KiB、总结果 2 MiB，子进程环境严格白名单。
4. 分词进程退出并 wait 后才调用原 `_infer_windows`；推理会话不重新加载。
   五次请求使用不同 PID，逐次断言 PID 消失，结束检查没有子进程和泄漏的快照 fd。
5. 加载完成即删除临时目录内五个模型硬链接，再发请求，验证不依赖源路径重开。
   只移除夹具链接，原模型内容和路径保留。不是跳过完整校验的元数据缓存。
   建立/删除硬链接会改变原 inode 的 nlink/ctime；本机测试模型属 nobody。
   该夹具不适用于跨文件系统或 root 只读生产部署。正式集成须直接校验原路径，
   不创建硬链接；生产模型归 root 的只读要求保持。

## 初验和补测

[初验](initial.txt)五次完整旧指纹通过，六个样例、16 个窗口；不重新生成期望值：
`6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`。
请求间模型进程 RSS 376556 KiB（约 368 MiB），请求 1.371–1.518 秒，
分词部分 0.821–0.925 秒，初始化 19.487 秒。上一轮常驻两对象构造后约 608 MiB；
这里观察到更低的空闲占用，不能拿两个独立运行的差值当成严格控制变量基准。

初验没有在分词进程存活时采样整组内存，所以只增加该观测后补测。
`group_memory_bytes` 来自当前测试单元的 `memory.current`，包含模型父进程、
分词子进程以及归属该组的缓存；没有把进程 RSS 相加冒充物理内存。
`peak_rss_kib` 和 `child_peak_rss_kib` 是 getrusage 生命周期高水位，包含 fork/exec
影响，不能解释为“exec 后分词对象独占内存”。运行中 RSS 是 `/proc/self/status`
样本，不是严格峰值；阶段采样也可能遗漏更短暂的峰值。补测不与初验累加分母。

[最终补测](measured.txt)仍为五次全部旧指纹通过：请求 1.379–2.092 秒，
分词 0.819–1.036 秒，初始化 23.319 秒。请求间模型 RSS 386460–386724 KiB
（约 377–378 MiB）；分词活跃时子进程 RSS 316056–316164 KiB、测试组采样
737427456–741163008 bytes（约 703–707 MiB），退出后约 413 MiB。
因此可以确认小夹具的请求间占用降低，但没有证明总峰值降低或完整组合可运行。
初始化及请求耗时包含观测开销、页缓存未受控，不是冷启动 SLA。

## 执行约束与剩余工作

使用原 `smartdocsvectorcheck.slice`，MemoryHigh=1200 MiB、Max=1600 MiB、Swap=0、
CPUQuota=100%、Tasks=256、Nice=10，读写各 5 MB/s、200 IOPS；每轮仅一个模型场景。
两个单元分别为 `smartdocs-transient-tokenizer-spike` 与
`smartdocs-transient-tokenizer-measured`，串行运行，RuntimeMaxSec=240、TimeoutStopSec=5、
KillMode=control-group，nobody/nogroup、PrivateNetwork=yes、AF_UNIX、NoNewPrivileges。
进程 AS=2 GiB、CPU=60 秒、core=0；启动文件限 256 MiB，加载后/分词请求限 2 MiB。
保持原共享模型锁，继承更严限制不放宽。脚本必须在这样的受限单元执行：

```bash
PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python \
TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
/tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python -B \
  /tmp/smartdocs-safe.h4SzvX/transient-tokenizer/spike.py \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
```

这不是可上线的服务：原型对分词有 60 秒 communicate 和最多 5 秒 wait，
对整个请求只有完成后的 `<60 秒` 断言，没有实现独立推理 watchdog。
已有正式 supervisor 的全请求截止期尚未接入本原型，不把事后计时当超时保护。
同组子进程便于整组终止，但本轮未注入挂起/崩溃/取消，不声称故障回收已验收。
尤其 `kill()+wait(5)` 仅针对直系子进程；D 状态和后代清理仍依赖外层整组监督，
不能将正常路径 PID 消失解释为这些故障路径已经验证。

下一增量才将机制接入现有可选常驻服务：保留原监督总截止期和进程组；
补分词挂起、坏输出、超限、关闭 fd 和异常回收的失败测试，跑完整 Python 与真实
私有服务，再做原浏览器组合。不得直接部署原型、提高限额或放宽来源/权限断言。
不更换模型/依赖，无远程 API 调用；全阶段剩余门槛见 [M2 索引](../README.md)。

两单元最终均退出码 0、inactive/dead，运行中的 smartdocs 单元为 0；原 MySQL
active，见[终态](final-status.txt)。memory.high 68423→68873→68978，max/oom/oom_kill=0。
这不是“无压力”，而是压力受限且未发生 OOM。无生产实现变化，未重跑未修改的
全量 Python/C++/前端套件。[最终源码指纹](source-sha256.txt)与执行副本 cmp 一致。
初验 spike.py 指纹为 `f9d3323bac3f89a60bf1857ef64d01b36745cc0a088f9a7924b7419fed3675b8`；
最终版本只增加 `tokenizer_live` 内存输出并将该输出送 stderr，不改变编码。
独立审查提出的硬链接元数据副作用及截止期证据边界已明确记录，复核无剩余
Critical/Important；这不等于原型达到生产服务验收标准。
