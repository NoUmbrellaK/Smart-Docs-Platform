# 两阶段编码器组合冷加载归因

2026-10-18，基线 `2379bfd527ea919ce7e915ecd9e5664a2b82301c`。
仅增加诊断包装与证据；生产代码、模型、资源上限和超时均未修改。
当前 ECS 的单次组合仍 **0/1，183.970 秒**：首次编码超时，唯一一次显式
API 重试成功发布真实向量，随后首次 `/app.html` 导航等待 WebDriver 响应超时。
尚未执行搜索/来源/新版断言，没有本轮搜索截图；M2 未验收，不能拼接旧轮成功。
原始终态见 [组合日志](browser-combination.txt)。

## 新证据

探针复用正式服务、锁、子进程、输入输出限额和共享截止期，只在原调用处记录
阶段、时间与进程资源数值，不记录正文、向量、凭据或异常原文。
读取 [encoder.jsonl](encoder.jsonl) 时须绑定 [源码指纹](source-sha256.txt)。
trace 行事件发生在该行执行之前；子进程 elapsed 分别从零开始。

| 观测 | 首次请求 | 唯一显式重试 |
| --- | ---: | ---: |
| 分词 parent_spawn 至 parent_result | 18.025 秒 | 8.211 秒 |
| 分词 helper 导入（265→266） | 6.393 秒 | 1.920 秒 |
| 分词加载（266→267） | 1.748 秒 | 0.609 秒 |
| 分词包装退出标记至父进程接收结果 | 6.464 秒 | 3.422 秒 |
| 推理启动时剩余预算 | 41.974 秒 | 51.788 秒 |
| 推理子进程启动至模型校验完成（288） | 38.128 秒 | 30.020 秒 |
| 会话构建到完成（296→298） | 未完成即超时 | 2.705 秒 |
| 首窗口推理（309→311） | 未到达 | 0.013 秒 |

首次推理到校验完成，累计 CPU 仅约 0.832 秒、read_bytes 约 159 MiB；
重试对应约 0.824 秒、71 MiB。结合共享组节流，证据指向冷加载/文件读取与
资源竞争消耗墙钟预算，而非模型计算慢。但不能仅靠这些数值精确区分页缓存、
I/O 限流、内存回收和调度各占多少；read_bytes 也不是模型文件总大小。
分词包装退出后的数秒间隔仍包含解释器收尾、管道和父进程调度，尚未归因。
重试客户端观测总时长 43.516 秒；第一次约 61.400 秒后收到超时。

## 测量限制与安全边界

- `sys.settrace`、读取 `/proc/self/io`、写日志都会增加开销，本轮不是生产 SLA 基准。
- parent_spawn 日志在剩余预算计算后写入，日志开销没有再扣除；最终截止期检查
  仍拒绝迟到成功，但失败/清理墙钟可能略晚。客户端日志在原请求计时器启动前，
  不能把两次 epoch 差当成精确 60/75 秒边界。
- CPU、RSS、major_faults、read_bytes 是各进程累计值；阶段增量用同进程相邻记录。
  不把各进程 RSS 峰值相加当同时驻留值。SIGKILL 不会生成 child_wrapper_exit。
- 空输入探针输出 embedding_invalid_input，退出码 0，未加载模型；见
  [客户端与烟雾记录](client-and-smoke.jsonl)。独立只读审查指出上述观测边界，
  未发现 Critical/Important 代码问题；审查不替代运行验证。

仍在原 smartdocsvectorcheck.slice：MemoryHigh=1200 MiB、MemoryMax=1600 MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，读写各 5 MB/s、200 IOPS。
非 root、Nice=10、KillMode=control-group，临时 MySQL buffer pool 32 MiB。
编码监听单元 900 秒，组合单元 780 秒；受保护 Unix socket 0600/父目录 0700，
编码服务 PrivateNetwork、AF_UNIX、NoNewPrivileges；浏览器保留原 Linux 沙箱。
先确认没有其他重型测试，再在同样受限的执行副本中使用包装入口：

```bash
# 以下是受限单元内部命令，不应直接在不受限 shell 启动重型测试。
# 编码服务的原 socket-activate --accept --inetd 调用改为以下 service 入口：
PYTHONPATH=python /path/to/parser-python -B /path/to/probe.py service \
  /private/encoder.jsonl /path/to/model /path/to/model-python /private/model.lock
# 原测试命令加 client 包装；原 SMARTDOCS_* socket、Chrome、driver、输出环境不变：
PYTHONPATH=python scripts/test-with-mysql.sh -- /path/to/parser-python -B \
  /path/to/probe.py client /repo/test/e2e/hybrid_browser_test.py /private/client.jsonl
```

包装代码见 [embedding_stage_probe.py](embedding_stage_probe.py)。不改变实际业务
调用次数，失败后仍只有测试原有的一次显式重试；没有远程 API/DeepSeek 调用。
本轮不重跑未修改的完整单测，也不宣称生产缺陷已经修复。

memory.high 从 55921 到 59471（+3550），max/oom/oom_kill 均为 0。
两临时单元已停止，原 MySQL active，清理后 MemAvailable 1900512 KiB；
详见 [终态](unit-status.txt)。原脚本清理了本轮人工数据的临时数据库，
不保留恢复副本，不影响原业务库。未读取或归档可能带凭据的 driver.log。

## 下一步

不再原样重跑整场。先验证直接使用已安装 tokenizer/NumPy 能否避免 FastEmbed
顶层导入图，保持特殊 token、完整窗口、归一化及全部模型哈希校验不变；
候选必须与既有 6 样例/16 窗口完整输出指纹一致后才能接入。
这只能减少已观测的导入成本，尚不能承诺消除主要的模型校验读取耗时；
首次页面导航的独立超时也仍需解决。不得以扩大内存/IO 限额或跳过模型校验换绿。
