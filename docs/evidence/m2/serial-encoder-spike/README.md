# M2 分词器生命周期与串行编码原型

2026-10-18，基线 `be1533c8612a9de320877b9e649cdd0282cf2e2b`。
仅验证可行性，未修改或安装生产编码器；M2 仍未验收。

## 分词器生命周期

固定模型使用 Unigram。[上游实现](https://github.com/huggingface/tokenizers/blob/v0.23.3/tokenizers/src/models/unigram/model.rs)
在构造中建立词表映射及 trie。本轮未找到能直接降低初始占用的公开缓存开关。
生命周期探针依次加载原生 Tokenizer、释放它、再通过现有 FastEmbed helper 加载，
用 5 个固定样例比较关闭截断/填充后的 token IDs，结果完全一致。

- 原生分词器加载后 RSS 363804 KiB，仍存活时 gc+malloc_trim 后 364308 KiB。
- 删除对象后仍为 347412 KiB，再 trim 才降至 77628 KiB。
- FastEmbed helper 加载并编码后 385448 KiB；对象仍存活时 trim 后 368912 KiB，
  删除对象并 trim 后 77788 KiB。

因此简单 gc/trim 不能释放仍存活的分词器；进程退出能避开分配器保留问题。
这是阶段 RSS，不是词表独占内存。与上轮约 229 MiB 的阶段增量不同，
因为分配顺序与已有堆空间不同，不能把增量当作恒定对象大小。
[原始日志](tokenizer-lifetime.txt)及[探针](smartdocs-tokenizer-lifetime.py)。
ctypes/malloc_trim 只出现在生命周期诊断中，不进入串行原型或生产代码。

## 串行编码原型

先让独立进程完成真实分词和窗口划分，收集 token IDs/attention mask，确认子进程退出；
随后主进程加载相同 ONNX 文件并逐窗口推理。使用原有 FastEmbed 分词 helper、
mean_pooling 及原显式 L2 归一化；相同依赖版本、模型 SHA、CPU provider、单线程、
关闭 CPU arena 和完整结果验证。未改模型、词表或索引修订，未伪造向量。

| 同一组 6 个样例 / 16 个窗口 | 现有 _encode 基线 | 串行原型 |
| --- | ---: | ---: |
| 主进程峰值 VmHWM，KiB | 662124 | 501316 |
| 分词子进程峰值，KiB | 不适用 | 380652 |
| 分词退出时主进程 RSS，KiB | 不适用 | 23988 |
| 全过程墙钟秒 | 2.870 | 6.347 |

完整 JSON 结果 SHA-256 均为
`6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`；
原型明确断言与基线相等。覆盖短英文、960 字中文、Unicode/组合字符/emoji/空白、
特殊 token、中文查询和 1200 字符重复串。分词子进程在模型加载前退出，
主进程峰值降低约 157 MiB（约 24%）；不能把两个进程 RSS 峰值相加当作实测物理占用，
也不是共享 cgroup 峰值、冷启动或浏览器共存性能证明。每组只运行一次，不作稳定加速结论。
[基线](baseline.txt)、[候选](candidate.txt)、[原型脚本](smartdocs-serial-encoder.py)。

## 未验证边界与下一步

这是硬编码人工输入的诊断脚本，不接受真实业务请求，不能直接作为生产服务：
尚未设计/验证统一 60 秒总截止期、两阶段中间结果校验、超时取消与后代进程回收、
模型变更竞态和故障发布路径。它给分词子进程单独 60 秒，整单元 180 秒；
不等同于原生产请求的 60 秒全程监督。正式实现尤其不能简单嵌套当前
_run_child：新会话孙进程可能逃离外层进程组回收；应由同一监督层串行管理两个子进程，
共享锁及剩余时间预算，测试任一阶段超时均不遗留进程或发布部分结果。
下一增量应将这个已测可行的方案接入正式编码器并做失败优先回归，不再调浏览器启动顺序。

## 环境与复现

脚本首行参数检查与日志首行给出解释器、源码副本、固定模型、共享锁和模式。
使用 `python -I -B`：生命周期脚本接收 code_dir/model_dir/lock；编码原型再接收
baseline 或 serial 和预期 SHA（基线用 -）。tokens 是内部子进程模式。
从归档位置运行串行脚本时，子进程通过 __file__ 使用同一脚本，无另一固定脚本路径依赖。

全部串行运行于 smartdocsvectorcheck.slice：MemoryHigh=1200MiB、MemoryMax=1600MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，磁盘读写各 5 MB/s、200 IOPS。
单元 User=nobody、Group=nogroup、Nice=10、PrivateNetwork=yes、
RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes、UMask=0077、RuntimeMaxSec=180、
TimeoutStopSec=5、KillMode=control-group、RemainAfterExit=yes；
内部保留 2 GiB 地址空间/CPU 限制和断网检查。复现前必须核对限额，不在无限额进程执行。

执行副本 embeddings.py 与工作树 cmp 一致。本轮三单元均退出 0 后停止；
memory.high 前后均 47501，max/oom/oom_kill 均 0，[终态](final-status.txt)无运行中
临时单元、原 MySQL active。未启动浏览器/临时数据库、未调用 DeepSeek、未读密钥，
未运行完整应用回归或组合验收；不足以宣称 M2 完成或服务器问题已解决。
