# M2 CPU 语义向量可行性验证（非生产实现）

日期：2026-10-17。业务代码基线 `16f794d6c175bfbab8c1030d87b6affce2888bcf`。
这是方案 A 第 5 步接入前的选型证据；探针是一次性实验程序，不能当成可部署
的编码器、向量服务或 M2 验收。没有改变业务代码、生产依赖或 MySQL schema。

## 结论

当前 ECS 可以在原有限额下运行这个候选的单线程 CPU 推理，不需要为了这次
探针立即扩容或另买 API 凭据。但不能直接接入并宣布完整向量检索：

- 最终两次新进程的峰值 RSS 为 **752040 / 753040 KiB（约 734.4 / 735.4 MiB）**。
  现有 `python/smartdocs/worker.py` 有 **512 MiB 虚拟地址空间硬限制**，因此不能
  把这次结果解释为该解析 worker 可以直接加载模型。RSS 和虚拟地址空间也不是
  同一指标；后续必须单独验证有界执行进程的资源方案，不抬高文件服务并发。
- 固定 6 段人工资料、4 个查询，两次首位命中均 **3/4**。中文“上线需要设置哪些
  参数？”误把交接资料排在部署配置之前；保留该失败，不换题凑成功率。
  这不是检索质量基准、30 项业务任务验收或 75% 的平台准确率。
- 本模型的默认分词窗口只有 **128 token**。1,200 字符的固定中文样例完整为
  **603 token**，库静默只编码前 **128 token**。该结果明确禁止标记为完整
  片段索引；正式适配需要不遗漏正文的有界窗口覆盖，并保留现有稳定片段 ID。
- 此模型路径返回未归一化的均值池化结果，样例原始范数约 **3.028–5.396**。
  余弦比较前必须显式归一化。最终输出是 384 维有限向量，归一化后单位范数、
  同进程重复编码在 `atol=1e-6` 下相同，两个不同资料的向量不相同。

推荐将其保留为可用候选，下一增量先实现并验证**完整窗口覆盖、显式归一化和
独立有界执行**；之后接入 C++ 权威任务/批准检查、持久向量和混合检索，再用
固定资料评估质量。不能把通过资源探针当成已完成这些工作。

## 真实来源、版本与费用

使用 [Qdrant 的量化 ONNX 移植](https://huggingface.co/Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q)，
固定修订 `faf4aa4225822f3bc6376869cb1164e8e3feedd0`；模型卡标注 Apache-2.0。
[原模型卡](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)
说明 384 维、均值池化及 128 token 窗口；
[FastEmbed 支持列表](https://qdrant.github.io/fastembed/examples/Supported_Models/)
包含该多语言模型。未使用聊天回答、随机数或词频哈希冒充语义向量。

实际运行：FastEmbed **0.9.0**、ONNX Runtime **1.23.2**、NumPy **2.2.6**、
tokenizers **0.23.3**，CPython **3.10.12**，CPUExecutionProvider，threads=1，
batch_size=1，不启用并行编码。全部 **34 个 wheel** 的精确版本及 SHA-256 见
[requirements-spike.txt](./requirements-spike.txt)，仅适用于本次 CPython 3.10/Linux
x86_64 实验，不加入生产 `python/requirements.txt`。

模型文件 `model_optimized.onnx` 为 **235052644 字节**，SHA-256
`634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99`；
分词器为 **17083009 字节**，SHA-256
`fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00`。
其余三个 JSON 配置的校验值也固定在探针内，加载前全部校验。

准备阶段只向 PyPI/Hugging Face 下载公开依赖和模型。推理阶段在
`PrivateNetwork=yes` 下执行，进程内断言仅存在 `lo` 接口；同时启用
`HF_HUB_OFFLINE=1`、禁止遥测/隐式凭据并使用本地模型目录。
没有读取 DeepSeek 配置、调用付费推理/向量 API 或发送业务资料。此处没有
新增 API token 消耗，但仍消耗本机 CPU、磁盘和下载流量。DeepSeek 的聊天
凭据不构成向量能力已验证的证据。

## 测量与证据

最终原始结果：[trial-1.json](./trial-1.json)、[trial-2.json](./trial-2.json)。
最终探针与执行副本逐字节一致，`probe.py` SHA-256 为
`12417c7dd9e65e589c47de52a11ad5b3ebc01364343158a3025878de6dd86c66`。

| 计时区间（秒） | trial 1 | trial 2 |
| --- | ---: | ---: |
| Python 依赖导入 | 1.582 | 0.316 |
| 模型加载 | 1.096 | 1.152 |
| 首轮 10 条短文本编码 | 0.116 | 0.118 |
| 同进程再次编码同 10 条 | 0.113 | 0.112 |

以上计时不含下载、包安装、模型校验或数据库/HTTP。加载前已顺序读取模型做
哈希校验，文件缓存可能预热；不声称这是冷启动、端到端延迟、长文档处理速度
或并发性能。两次进程 CPU 时间约 2.50 / 2.55 秒。

失败和探针调整均保留解释：初次把原始向量误认为单位向量，断言失败；核对
固定版本 `pooled_embedding.py` 后增加显式归一化。随后曾用第二份分词器检查
长输入，峰值约 1016 MiB；最终探针复用同一分词器并恢复原截断设置，避免
把诊断副本的内存开销误当成服务必需内存。该早期数值不混入最终两次结果。

两个最终单元为 `smartdocs-m2-vector-probe-3`、`smartdocs-m2-vector-probe-4`，
均 Result=success / ExecMainStatus=0 / ActiveState=inactive；启动时核对
PrivateNetwork=yes、User=nobody、MemoryMax=1677721600、CPUQuotaPerSecUSec=1s。
退出后 systemd 会回收 transient 配置，不能用回收后的默认属性推断运行配置。
结束后原 mysql.service=active，可用内存采样约 2.0 GiB。

## 复现和资源边界

本机：约 3.5 GiB RAM、无 swap、2 个逻辑 CPU，x86_64；每次仅一个重型单元。
使用 nobody/nogroup、MemoryHigh=1200M、MemoryMax=1600M、MemorySwapMax=0、
OOMPolicy=stop、CPUQuota=100%、Nice=10、TasksMax=256、RuntimeMaxSec=900，
/dev/vda 读写各 5M 和 200 IOPS。可用内存低于 512 MiB 时停止自己启动的单元。
探针另设置 60 秒 CPU 软限制/65 秒硬限制。不要在未限额的业务进程中运行。

实验根目录为 `/tmp/smartdocs-safe.h4SzvX/vector-probe`，约 **547 MiB**，保留了
隔离 venv、wheel 与模型供后续验证；未安装常驻服务。模型目录仅含以下文件：
`config.json`、`special_tokens_map.json`、`tokenizer_config.json`、`tokenizer.json`、
`model_optimized.onnx`。下载 URL 固定到上述 Hugging Face 修订的 `/resolve/` 路径。

在有网络的受限准备单元内，可用 `python3 -m venv --without-pip` 建立独立环境，
再用系统 pip `--isolated --only-binary=:all: --require-hashes` 安装上述锁定文件到
该 venv 的 site-packages，禁止改系统包或现有解析环境。下载后核对探针中的全部
模型哈希。把 [probe.py](./probe.py) 复制到非 root 可读位置，再在相同限额且
`PrivateNetwork=yes` 的**独立后续单元**中运行：

```bash
# 这些变量不包含凭据；设置在受限单元里，不覆盖 HOME/CODEX_HOME。
HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  /path/to/probe-root/venv/bin/python -I -B /path/to/probe.py /path/to/probe-root
```

进程输出 JSON 到单元日志。只使用脚本内预先固定的人工样例，不接受用户文档。
正式向量化仍必须由 C++ 重新核对具体版本批准、当前权限、删除和版本状态；
本地模型不能成为绕过业务批准的理由。没有实现或验证混合检索、撤销竞态、
索引发布、数据库容量或 Agent 外发路径，**M2 仍未完成**。
