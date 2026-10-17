# M2 短生命周期分词正式集成

2026-10-18，北京时间；实现基线 `13c1c132c451d94518fef8c231c72d1ad1e8181c`，
被测源码见 [SHA 清单](source-sha256.txt)，已与执行副本逐文件 cmp 一致。
这是可选常驻编码入口的实现增量，不是生产部署或 M2 阶段验收通过。

## 实现与边界

- 全部五个模型文件完整 SHA 校验并封存后才构造 ONNX 会话。仅保留两份 sealed
  tokenizer/special-tokens 字节快照，共 17083973 bytes；不保留原生分词器堆。
- 每请求启动分词子进程，只传两份 fd 和白名单环境；再次校验 seal/SHA，输入
  64 KiB、准备结果 256 KiB，结果经结构验证才推理。子进程退出并 wait 后返回。
- 子进程继承模型进程组，沿用未修改的 resident_service 总 60 秒截止期及
  killpg/有界回收；没有新增重试、远程请求、依赖或索引修订。
- 两个 fd 经显式 close/finalizer 或进程退出释放。生产实现不创建模型硬链接；
  root 只读模型部署要求保持。真实核心测试沿用 nobody 模型硬链接夹具，删除的
  只是夹具链接，原文件内容不变，但 inode nlink/ctime 会改变，不可照搬到生产。
- 裸用核心只有分词 helper 超时，没有推理 watchdog；应使用受监督的私有服务。
  内核 D 状态无法保证在 5 秒内消失，旧 cgroup 未空不得重启另一个模型。

## 本轮证据

[初始失败](initial-red.txt)：新增五项测试因缺少有界 helper 明确失败，随后实现。
[聚焦测试](focused-tests.txt)：分词边界 **6/6**，监督服务 **10/10**；覆盖实际
SIGSTOP 子进程、坏输出、超大输出、崩溃、安全错误、拒绝非模型封存字节、fd 关闭。
新增监督场景把分词孙进程挂起，验证同一模型 PGID、原总截止期返回 timeout、
监督层退出且模型与分词进程均消失。假 token ID 只用于协议/回收测试，不作质量证据。

[完整 Python 回归](python-regression.txt)：**102/102**，21.374 秒，0 失败。
聚焦套件包含在此分母内，不累计。C++/JS 本轮未修改、未重跑。

[真实核心和私有服务](real-model.txt)均为非 root、禁用 IP socket、原共享资源限额：

| 场景 | 结果 | 时间与内存样本 |
| --- | --- | --- |
| 核心，9 项检查 | 五次旧指纹一致，源夹具链接删除后仍可编码；仅两份 sealed fd，分词进程消失，close 关闭 fd | 初始化 8.012 秒；请求 1.378–1.579 秒；请求后 RSS 382280 KiB（约 373 MiB），父进程高水位 471476 KiB |
| 私有服务，7 项检查 | 三次同一模型子进程、旧客户端及旧指纹；拒绝错误输入后可复用，SIGTERM 回收模型 | 初始化 2.822 秒；三次计时请求 1.416–1.763 秒；模型 RSS 384852 KiB（约 376 MiB），模型高水位 473844 KiB |

旧指纹固定为 `6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`，
六个样例、16 个窗口，不重生成期望值。请求间 RSS 低于旧双对象常驻诊断约
608 MiB，但不同运行不作为严格控制变量基准；RSS/高水位不包含整组全部内存。
本轮未对分词活跃时整组峰值采样，不把父进程高水位当成总峰值。
页缓存未受控，初始化时间不是冷启动 SLA；没有浏览器/数据库组合通过声明。

## 复现约束和清理

执行副本 `/tmp/smartdocs-safe.h4SzvX/repo`，原共享切片
`smartdocsvectorcheck.slice`：MemoryHigh=1200 MiB、Max=1600 MiB、Swap=0、
CPUQuota=100%、Tasks=256、读写各 5 MB/s、200 IOPS。各测试串行，nobody/nogroup、
Nice=10、NoNewPrivileges=yes、KillMode=control-group、TimeoutStopSec=5。
真实核心 RuntimeMaxSec=240，私有服务 300；均 PrivateNetwork=yes、
RestrictAddressFamilies=AF_UNIX，UMask=0077，离线/单线程环境。
完整 Python 回归使用解析环境，不加载真实模型；真实场景命令为：

```bash
# 仅在上述受限单元中执行，不直接以 root 启动。
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  -m unittest discover -s test/python -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python -B \
  test/e2e/m2_resident_embeddings_test.py /tmp/smartdocs-safe.h4SzvX/vector-probe/model
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_resident_service_test.py /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python
```

核心测试不获取共享模型锁，私有服务夹具使用自身临时锁，因此本轮先确认无其他
smartdocs 单元再串行启动，不可将这两个夹具并行。生产监督入口仍持有配置的共享锁。
全部五个单元 exit 0、inactive/dead，运行中的 smartdocs 单元为 0，原 MySQL active；
见[终态](final-status.txt)。本轮观测 memory.high 68978→68978，max/oom/oom_kill=0。
未扩容、未改资源上限、未调用 DeepSeek 或其他远程 API。

独立静态审查未发现 Critical/Important；确认校验先于加载、fd 生命周期、
结果边界和原监督层对子孙进程的回收关系。保持最小实现：只替换已有核心的分词
生命周期，复用原推理、协议和监督层，不另建服务框架。
下一步以本实现运行原浏览器组合，不降低身份/来源断言；三格式永久日志、双页
PDF/文本行出处和业务失败→人工重试联合验证仍见 [M2 索引](../README.md)。
