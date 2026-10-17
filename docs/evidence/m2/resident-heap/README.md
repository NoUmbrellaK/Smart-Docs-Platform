# M2 常驻模型堆内存归因：不采用简单 trim

2026-10-18，北京时间；基线 `fa1d5fc3f57c9eb542428b6296cc2ff1f335056a`。
这是隔离诊断，不改生产实现、不重跑浏览器组合、不关闭 M2。

## 假设、方法与结果

假设：常驻核心加载后约 608 MiB RSS 中，大量内存已经释放但仍滞留于分配器，
可通过 GC 或 `malloc_trim(0)` 显著回收。实际结果否定了这个简单方案。

同一非 root、禁网进程加载原 ResidentEncoder，再依次 GC、trim、五次编码、trim。
最后仅在这个即将退出的诊断实例里删除分词器、再删除模型，各自 GC/trim，
用于观察存活对象贡献；删除后不再编码。每次编码都是原六样例、16 窗口，
完整输出必须等于既有 SHA `6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`。

| 实测阶段 | RSS (KiB) | 匿名内存 (KiB) |
| --- | ---: | ---: |
| 构造返回 | 622564 | 586576 |
| Python GC 后（收集 0 个对象） | 622564 | 586576 |
| 第一次 trim 后 | 622500 | 586512 |
| 五次请求后 | 633544 | 596464 |
| 请求后 trim | 624880 | 587800 |
| 删除分词器并 GC/trim | 339292 | 302212 |
| 再删除模型并 GC/trim | 66552 | 29472 |

加载后 trim 仅减少 **64 KiB**；请求后减少 **8664 KiB**。删除分词器才释放
约 **279 MiB** RSS，随后删除模型再释放约 **266 MiB**。这是一次顺序实验中的
条件差值，不是两个对象独立、精确、可相加的尺寸。不能以此推断长期无泄漏。
说明大头与仍存活的原生对象相关，而非 Python 未收集循环引用或大量可直接归还的
空闲整页。`mallinfo2` 仍报告约 30 MiB 空闲块，不等于这些块都能按页归还。
定义参见 [glibc 分配统计](https://sourceware.org/glibc/manual/latest/html_node/Statistics-of-Malloc.html)
与 [Linux malloc_trim 手册](https://man7.org/linux/man-pages/man3/malloc_trim.3.html)。

五次旧指纹全部通过，请求 0.559–0.615 秒；构造耗时 60.733 秒，进程峰值
639028 KiB。含探针开销、页缓存未受控，不是组合性能或 SLA。
原始结果见 [measurement.txt](measurement.txt)，执行脚本 [heap_probe.py](heap_probe.py)。
读取 `/proc/self/smaps_rollup` 与本机 glibc `mallinfo2`，只记录数量，不输出正文/凭据。

## 限额与复现

使用原 `smartdocsvectorcheck.slice`：MemoryHigh=1200 MiB、Max=1600 MiB、Swap=0、
CPU=100%、Tasks=256、读写各 5 MB/s 和 200 IOPS、Nice=10；仅运行一个模型场景。
单元 `smartdocs-resident-heap-probe` 总寿命 240 秒、停止等待 5 秒、KillMode=control-group，
nobody/nogroup、PrivateNetwork=yes、AF_UNIX、NoNewPrivileges=yes、UMask=0077。
进程 AS=2 GiB、CPU=60 秒、启动 memfd 文件=256 MiB、加载后=2 MiB、core=0；
不放宽继承的更严上限。原模型共享 flock 全程保持，不修改模型文件。

受限单元内命令（不能直接以 root 运行）：

```bash
PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python \
TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
/tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python -B \
  /tmp/smartdocs-safe.h4SzvX/resident-heap/heap_probe.py \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
```

## 决定及后续边界

不把 GC/trim 加到生产路径：对初始化常驻占用没有明显收益。
下一项可验证候选是保留推理会话、将分词器限制在短生命周期子进程；它可能降低
空闲内存，但请求时总峰值不一定下降，必须先实验验证，不能承诺组合必过。
完整校验后的不可变字节、旧向量指纹、原 60 秒总截止期、后代回收及 C++ 权限
仍是不可降低的条件。不重新加载大模型，不使用“文件元数据未变就跳过 SHA”的缓存。
这是下一轮候选，不是本轮已实现能力；没有修改模型/索引修订或启用生产服务。

测试退出码 0，memory.high 68006→68423（+417），max/oom/oom_kill=0。
最终无运行中的 smartdocs 测试单元，原 MySQL active；详见[清理终态](final-status.txt)。
[源码指纹](source-sha256.txt)已核对，工作树与实际执行副本逐文件 cmp 一致。
没有生产代码变化，不重复运行未修改的 Python/C++/前端全量回归。
独立只读审查核对探针 ABI、删除顺序、日志数字和结论边界，无 Critical/Important。
未调用 DeepSeek。完整浏览器/数据库仍未通过，其他 M2 门槛见[阶段索引](../README.md)。
