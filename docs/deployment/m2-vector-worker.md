# M2 本机向量调度与隔离编码服务

本部署手册负责 C++ schema 5 队列与固定的 MiniLM 编码器；搜索入口另见
[同源混合检索网关](m2-hybrid-search.md)。这些模板
不表示生产库已升级或生产服务已安装。先阅读 [解析 worker 手册](m2-local-worker.md)：
C++ 仍是版本、批准和任务状态的唯一权威，内部接口不经公网反向代理暴露。

## 进程边界

- `smartdocs.vector_worker` 是轻量可信调度器，只连接 `127.0.0.1` C++ 和固定本机
  Unix socket；512 MiB 地址空间，单实例 flock。独立锁不能与解析 worker 的实例锁
  共用；C++ 的全局 running 上限仍使两类任务串行。
- `smartdocs.embedding_service` 每个连接只接收一批 1–8 块，输入 64 KiB、输出
  2 MiB；客户端半关闭写端表示输入结束，服务退出关闭连接表示输出结束。
  不传用户身份、token、命令或模型路径；模型路径由 root 管理的服务配置确定。
- 编码服务无 C++ bearer、数据库密码或 API key，独立网络命名空间及 AF_UNIX
  地址族白名单。入口探测若能创建 IPv4/IPv6 socket 就拒绝运行；环境变量离线标记
  不是网络安全边界。每批由同一监督层串行运行分词、推理两个子进程，前者退出后
  才加载 ONNX；每个子进程仍限 2 GiB 地址空间，两阶段共享原 60 秒墙钟预算及同一锁。
  分词输出在 JSON 解析前限 256 KiB，推理输入也限 256 KiB；外部输入仍为 64 KiB。
  中间结果复查身份/文本摘要、连续完整区间、每窗至多 128 tokens、每批至多 128 窗及
  固定词表 ID/attention mask。ONNX CPU 内存池仍关闭，未部署常驻模型、未新增依赖。
  模型、池化、归一化与索引修订不变，兼容性与边界见
  [分阶段编码验证](../evidence/m2/staged-encoder/README.md)。
  当前直接用 tokenizers 加载固定特殊 token、NumPy 完成同样池化，避免 FastEmbed
  顶层运行时导入；仍保留其锁定安装/版本检查。换模型必须重新验证 map 结构与输出，
  见[最小导入回归](../evidence/m2/minimal-encoder-imports/README.md)。
- socket 归 worker 用户所有、0600，父目录 0700；客户端拒绝符号链接、其他所有者和
  组/全局可访问 socket。只允许同一可信服务账号使用它，不作为用户/Agent 编码 API。
  代码、模型和虚拟环境由 root 管理且对该账号只读；不授予业务文件目录访问权。

每批从 C++ 重新读取当前获批版本；结果校验摘要、覆盖区间、修订和 384 维单位向量，
再由 C++ 在事务内重新检查授权并推进游标。仅最后一批的 completed 确认才报告完成。
撤销或换版可能发生在本地编码途中，此时已开始的有界计算会结束，但结果不能发布。
不声称撤销能撤回已经开始的计算。

领取不重试；发布与失败回报仅允许一次原样重放。不可确认的 HTTP 错误使调度器退出，
不能推断事务失败并领取新任务。编码服务不可用/超时/异常显式 fail，保留关键词检索。
调度器先锁再恢复，只将向量 running 标记 interrupted，等待用户手工 retry；
不干扰解析任务。`--once` 处理一次领取的整个版本，不是仅一个批次。

## systemd 配置模板

这些是需审核和替换路径的模板，不是自动安装脚本。使用独立非 root 账号
`smartdocs-worker`。安装固定的 [可选依赖](../../python/requirements-embedding.txt)，
准备已校验模型，不允许运行中下载；复用当前主机资源测试后的模型配置。

在 `/etc/tmpfiles.d/smartdocs-encoder.conf` 创建受控运行目录（由管理员部署）：

```text
d /run/smartdocs-encoder 0700 smartdocs-worker smartdocs-worker -
```

`smartdocs-encoder.socket`：

```ini
[Unit]
Description=Smart Docs private encoder socket

[Socket]
ListenStream=/run/smartdocs-encoder/encoder.sock
SocketUser=smartdocs-worker
SocketGroup=smartdocs-worker
SocketMode=0600
Accept=yes
MaxConnections=2
Backlog=2
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
```

`smartdocs-encoder@.service`：

```ini
[Unit]
Description=Smart Docs bounded offline encoder

[Service]
Type=exec
User=smartdocs-worker
Group=smartdocs-worker
Slice=smartdocs-processing.slice
Environment=PYTHONPATH=/opt/smart-docs/python
ExecStart=/opt/smart-docs-venv/bin/python -B -m smartdocs.embedding_service /opt/smart-docs-model /opt/smart-docs-embedding-venv/bin/python /run/smartdocs-encoder/model.lock
StandardInput=socket
StandardOutput=inherit
StandardError=null
RuntimeMaxSec=75
TimeoutStopSec=5
KillMode=control-group
Restart=no
UMask=0077
NoNewPrivileges=yes
PrivateNetwork=yes
RestrictAddressFamilies=AF_UNIX
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/run/smartdocs-encoder
MemoryHigh=1000M
MemoryMax=1200M
MemorySwapMax=0
OOMPolicy=stop
TasksMax=32
Nice=10
```

两连接只容忍前一个进程退出交接的短窗口；共享 `model.lock` 保证仍只运行一个
模型子进程，竞争返回 embedding_busy，不并发加载模型。为整个处理组设总限额，
不能把各服务各自限额相加当作机器可用内存。

`smartdocs-processing.slice`：

```ini
[Slice]
MemoryHigh=1200M
MemoryMax=1600M
MemorySwapMax=0
CPUQuota=100%
TasksMax=192
IOReadBandwidthMax=/dev/vda 5M
IOWriteBandwidthMax=/dev/vda 5M
IOReadIOPSMax=/dev/vda 200
IOWriteIOPSMax=/dev/vda 200
```

向量调度器沿用解析 worker 的 systemd 模板和独立 bearer 文件，仅修改/增加：

```ini
[Unit]
After=smart-docs.service smartdocs-encoder.socket
Requires=smart-docs.service smartdocs-encoder.socket

[Service]
Slice=smartdocs-processing.slice
RuntimeDirectory=smartdocs-vector
ExecStart=/opt/smart-docs-venv/bin/python -B -m smartdocs.vector_worker
Environment=SMARTDOCS_VECTOR_LOCK_PATH=/run/smartdocs-vector/worker.lock
Environment=SMARTDOCS_ENCODER_SOCKET=/run/smartdocs-encoder/encoder.sock
```

这里指新建完整服务文件时替换对应项，不是直接可用的 drop-in（修改 ExecStart 的
drop-in 必须先清空旧值）。解析 worker 也应加入同一 processing slice。C++ 和 MySQL
仍需主机整体预算；不要并行进行构建/数据库/浏览器/模型重型验收。

先校验 unit 文件与目录权限，启动 C++ 并确认 readiness，再启用 socket 和两个 worker。
停止顺序：两个 worker → encoder socket 及已活动的 encoder 实例 → C++。
不能仅停止 socket 就假定活动实例已经停止。恢复按反向顺序，手动处理 interrupted。

## 验证

在隔离、非 root、受限环境运行，先启动禁止 IP sockets 的编码服务，并指定它的
受保护 socket。测试只使用人工文本，不读取 DeepSeek key：

```bash
PYTHONPATH=python /path/to/parser-venv/bin/python -B -m unittest discover -s test/python -v
SMARTDOCS_ENCODER_SOCKET=/controlled/runtime/encoder.sock PYTHONPATH=python \
  scripts/test-with-mysql.sh -- /path/to/parser-venv/bin/python -B test/e2e/m2_vector_worker_test.py
```

第二个命令需要已构建的 `bin/server` 与 `bin/smartdocs-admin`，只接受隔离测试 MySQL。
测试脚本固定关闭临时库的 performance_schema、将 buffer pool 设为 32 MiB，减少
与模型共存时的内存压力；这不是生产/吞吐量测试配置，不修改已有 MySQL 服务。
模型加载也计入 60 秒超时：限速磁盘和缓存状态会影响冷启动，超时需要查看任务
错误码并手动 retry，不靠自动无限重试或抬高主机限额掩盖问题。
它验证真实模型输出通过 C++ 向量校验并持久发布，不用人工单位向量冒充模型结果。
混合检索后端和网关已有独立增量；整个 M2 阶段综合验收仍未完成。
