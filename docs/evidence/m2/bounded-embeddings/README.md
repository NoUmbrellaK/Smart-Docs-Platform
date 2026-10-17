# M2 有界本地向量编码器

日期：2026-10-17；业务基线 `5ae59d30f5e135aac510d4be9e301c9137efac05`。
此增量仅交付可独立调用的本地编码器，不是已接通的向量索引/混合检索。
真实被测源码 SHA-256 和测试 SHA-256 记录于 [result.json](./result.json)。

## 本次真实结果

- 8 个编码器纯函数/真实小子进程测试通过；连同现有解析与 worker，最终
  Python 回归 **47/47** 通过（`smartdocs-m2-encoder-regression-2`）。
- 真实模型测试 [m2_embeddings_test.py](../../../../test/e2e/m2_embeddings_test.py)
  在 `smartdocs-m2-encoder-2` 下通过 9 类断言：Unicode 完整覆盖、真实 token
  预算、尾部改变影响向量、独立进程重复一致、单位向量、锁互斥、超时后重用、
  模型篡改拒绝、继承限制不放宽。两个单元均 Result=success / ExecMainStatus=0。
- 三个人工输入分别产生 8、8、4 个连续窗口，每窗实测 82–91 token（含特殊
  token），不再把长文本直接交给默认 128 token 截断。原片段 ID、文本摘要和
  全部字符区间保持对应；字符是 Python Unicode 码点，不是 UTF-8 字节或 UTF-16。
- 两次完整公开接口调用耗时 **3.392 / 3.238 秒**（包含启动、模型校验与加载、
  20 个窗口推理、父进程校验），子进程峰值 RSS **755144 KiB，约 737.4 MiB**。
  缓存可能已预热；不是冷启动、最大批次吞吐量或业务端到端性能承诺。
- 未读取 DeepSeek 配置、未调用付费 API、未发送业务资料、未修改生产数据库。
  结束后可用内存采样约 2.0 GiB；测试始终串行，没有提高主机并发。

初次测试在加载模型前因用 `/sys/class/net` 判断网络隔离而失败。单独诊断
确认 sysfs 仍显示宿主机接口，而 `socket.if_nameindex()` 在实际网络命名空间
只返回 `lo`；改用后者后通过，没有移除断网要求。审阅未发现关键/重要缺陷，
并补充了超时、正常退出和异常退出后的后代进程终止测试；测试自身的 `/proc`
读取与进程退出竞态已处理。没有把失败运行算入通过分母。

## 接口与边界

实现：[embeddings.py](../../../../python/smartdocs/embeddings.py)。调用示例仅表示
可信后台程序的 Python 接口，不是 HTTP、MCP 或允许模型执行的命令：

```python
from smartdocs.embeddings import encode_chunks, EmbeddingError

result = encode_chunks(
    authorized_chunks,  # [{"chunk_id": stable_64_lower_hex, "text": authorized_text}]
    model_dir="/srv/smartdocs-models/minilm-fixed",
    python_executable="/opt/smartdocs-embedding/bin/python",
    lock_path="/run/smartdocs-embedding/encoder.lock",
)
```

一次 1–8 个片段，每片段 1–1200 字符；输入 JSON 不超过 64 KiB。禁止重复 ID、
无文本、非法 UTF-8 或控制字符。关闭分词器截断/填充，以真实 token 数判断；
超预算区间按字符中点递归二分，不依赖分词长度单调性。每窗至多 128 token，
每片段至多 64 窗、每次至多 128 窗。超过限制整批失败，不返回部分完成。
这是简单、确定的覆盖策略，不承诺最佳语义切分，未实现重叠窗口或召回重排。

输出固定 `embedding_revision`、原 ID、UTF-8 文本 SHA-256，以及每窗相对原片段
的 `[char_start,char_end)` 和 384 维向量。显式 L2 归一化；父进程独立验证结构、
摘要、顺序、完整连续区间、维数、有限值与单位范数。输出至多 2 MiB。不返回正文，
错误仅用固定应用代码，不返回子进程异常、模型路径或 stderr。

父/子进程均要求非 root。所有编码调用必须使用同一个受保护锁文件，以现有
`worker_lock` 实现互斥；锁保持至子进程结束并回收，繁忙直接失败，无自动重试。
这是复用锁实现，不应借用常驻解析 worker 已终身持有的锁文件。部署方还须协调
不同重型服务，单个编码锁并不能限制其它程序的资源使用。

子进程 AS 上限 2 GiB、CPU 60/65 秒、文件大小 2 MiB、禁止 core dump；不放宽
继承的软/硬限制。默认墙钟 60 秒，调用者只能缩短；超时杀整个进程组并回收。
正常/失败退出也清理同组后代。现有解析 worker 的 512 MiB AS 限制完全未改，
不能把该编码器直接塞入其中；需要后续独立执行单元/调度集成。

路径只能来自可信部署配置，不能来自文档、模型工具参数或用户 HTTP 字段。
模型目录应由部署账号维护、对运行账号只读；五个文件每次加载前验证固定 SHA-256，
并核验关键库版本。部署账号和同 UID 被攻陷不属于这些校验能隔离的威胁范围。
子进程不继承凭据/代理环境，使用离线库设置；**这些设置不是网络安全沙箱**。
正式运行必须同测试一样使用 OS 网络隔离，按后续 IPC 方案集成，当前未安装常驻服务。

## 依赖和复现

可选运行时锁：[requirements-embedding.txt](../../../../python/requirements-embedding.txt)，
34 个精确 wheel 版本/哈希沿用已验证探针，适用于 CPython 3.10/Linux x86_64。
以 `--only-binary=:all: --require-hashes` 安装到独立 venv；不混入解析器的
`python/requirements.txt`。固定模型修订、公开下载来源、五个文件名与准备方式见
[选型证据](../vector-probe/README.md)。模型或运行时升级须重新测量并更新编码修订。

当前保留的临时根为 `/tmp/smartdocs-safe.h4SzvX`：`repo` 是被测源码副本，
`vector-probe/model`、`vector-probe/venv` 是固定本地模型及隔离运行时，`venv`
为现有解析测试环境。只在没有活跃重型单元时同步源码。复现真实模型测试：

```bash
systemd-run --unit=smartdocs-embedding-check \
  -p User=nobody -p Group=nogroup -p MemoryHigh=1200M -p MemoryMax=1600M \
  -p MemorySwapMax=0 -p OOMPolicy=stop -p CPUQuota=100% -p Nice=10 \
  -p TasksMax=256 -p RuntimeMaxSec=900 -p PrivateTmp=yes -p PrivateNetwork=yes \
  -p BindPaths=/tmp/smartdocs-safe.h4SzvX \
  -p 'IOReadBandwidthMax=/dev/vda 5M' -p 'IOWriteBandwidthMax=/dev/vda 5M' \
  -p 'IOReadIOPSMax=/dev/vda 200' -p 'IOWriteIOPSMax=/dev/vda 200' \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python -B \
  /tmp/smartdocs-safe.h4SzvX/repo/test/e2e/m2_embeddings_test.py \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python
```

启动后及时核对 User、PrivateNetwork、MemoryMax、CPUQuotaPerSecUSec；临时单元
退出后配置会被回收。通过 journal 读取输出，并以 Result/ExecMainStatus 判断。
主机可用内存低于 512 MiB 时停止自己启动的单元，不并行构建、数据库或浏览器负载。
Python 回归在相同限额下、源码副本目录运行：

```bash
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  -m unittest discover -s test/python -v
```

## 尚未完成

没有向量持久化、混合排名、业务 HTTP/UI 接入或生产部署。该组件不负责授权，
未来调用前/发布前/读取时仍须由 C++ 检查项目权限、具体版本批准、当前版本、删除、
取消及 attempt；只允许符合方案 A 边界的资料进入向量流程。不能因本地执行而
绕过这些规则。没有新增数据库迁移，也未重跑 C++/浏览器全量验收。
资源可行性和覆盖测试不证明召回质量；之前 4 个查询中 1 个失败的选型结果仍保留。
M2 尚未完成，下一增量才接权威任务与持久结果，之后进行真实混合检索验收。
