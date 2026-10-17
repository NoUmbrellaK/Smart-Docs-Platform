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

### 可选常驻服务（需单独验收，不默认启用）

`smartdocs.resident_service` 可复用已经完整 SHA 校验并封存加载的模型，沿用同一
`request_encoding` 客户端协议、输入/输出上限及索引修订。它是上述逐请求服务的
替代部署方式，不能两者同时启用；当前代码不自动安装或切换任何生产服务。
当前采用推理会话常驻、分词子进程逐请求退出的机制；真实核心小夹具请求间
RSS 约 373 MiB（旧双对象常驻约 608 MiB），不是整组峰值或组合容量承诺。
不能套用旧分阶段约 461 MiB 的峰值预算。
是否能与浏览器/数据库在共享配额内稳定运行仍须组合验证。

启动时仍对全部五个模型文件完整 SHA 校验并封存，全部成功后才加载 ONNX。
仅保留 tokenizer 和 special-tokens 两个 sealed memfd（共 17083973 字节），
每请求分词子进程再次核对封印及 SHA，退出并回收后才执行推理。
不重新打开模型源路径，不在生产实现创建硬链接；模型 root 只读部署要求不变。
两个 fd 在核心 close/finalizer 或进程退出时释放，不保留原生分词器对象。

监督进程持有原 `model.lock`，只启动一个模型子进程。启动最多 180 秒，接收到
模型就绪帧后才创建监听 socket 并通知 systemd READY；启动未完成不接业务请求。
每次接收请求起共用 60 秒墙钟截止期，包括输入、编码、输出；同时到来的连接
在当前请求 deadline 内、最多 1 秒有界读取后返回 busy；慢速/超大竞争连接关闭，
不承诺它们收到完整错误体，不形成无界队列。超时、子进程退出或非法 IPC 输出使监督层杀死模型
进程组（包括同组的短生命周期分词进程）并 wait 回收，然后退出；不自动重放当前请求，不自动重启模型。
SIGTERM 同样回收子进程，systemd 必须保持 KillMode=control-group。
发出 SIGKILL 后最多等待 5 秒；如果内核不可中断 IO（D 状态）阻止及时退出，则
服务失败，不宣称已经回收。管理员必须确认整个旧服务 cgroup 已空再人工重启，
不能仅凭父进程退出开始加载另一个模型；systemd 停止期限仍是 5 秒，与应用回收
等待相同，异常路径可能由 systemd 接管进程组终止和 RuntimeDirectory 清理，
不保证监督进程来得及写完终态日志。目录被清理也不证明 D 状态进程已经退出。

模型子进程只继承离线/单线程白名单环境，不继承 bearer、数据库密码或 API key。
地址空间最多 2 GiB，memfd 启动期文件上限 256 MiB、加载结束降至 2 MiB，禁用
core dump，继承更小限制不会放宽。常驻进程不能套用旧子进程的累计 60 秒 CPU
寿命限制；采用原共享 CPUQuota 和监督层每请求墙钟上限。不要添加 LimitCPU=60，
也不要用增加主机配额掩盖资源争用。监督层自身地址空间在创建子进程后限制为 512 MiB。
逐请求分词子进程另有原 CPU 60/65 秒及文件 2 MiB 上限，但整体请求仍受监督层
同一个 60 秒墙钟截止期约束，不是分词和推理各 60 秒。裸用 ResidentEncoder
仅限制分词 helper，不能代替服务的推理 watchdog。实现与故障测试见
[正式集成证据](../evidence/m2/transient-integration/README.md)。

示例完整 `smartdocs-encoder-resident.service`（先替换并核对路径）：

```ini
[Unit]
Description=Smart Docs verified resident encoder

[Service]
Type=notify
NotifyAccess=main
User=smartdocs-worker
Group=smartdocs-worker
Slice=smartdocs-processing.slice
RuntimeDirectory=smartdocs-encoder-resident
RuntimeDirectoryMode=0700
Environment=PYTHONPATH=/opt/smart-docs/python
ExecStart=/opt/smart-docs-venv/bin/python -B -m smartdocs.resident_service /run/smartdocs-encoder-resident/encoder.sock /opt/smart-docs-model /opt/smart-docs-embedding-venv/bin/python /run/smartdocs-encoder/model.lock
TimeoutStartSec=190
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
ReadWritePaths=/run/smartdocs-encoder /run/smartdocs-encoder-resident
MemoryHigh=1000M
MemoryMax=1200M
MemorySwapMax=0
OOMPolicy=stop
TasksMax=32
Nice=10
```

保留前述 tmpfiles 0700 目录以共用旧模型锁。切换时先停止 worker、网关、旧 socket
和所有活动旧实例，确认回收后启动常驻服务；只有其 systemd 状态 active（READY）
才启动消费者。消费者将 `SMARTDOCS_ENCODER_SOCKET` 改为新路径，依赖关系改为
`After=/Requires=smartdocs-encoder-resident.service`（在各字段中填服务名称，不是
把此说明作为一行 unit 配置）。停止按消费者→常驻服务的顺序；故障后人工查看
任务真实状态，启动新服务并对具体失败任务手动 retry，不自动提交旧结果。

核心加载后不再打开源文件；替换固定模型/依赖必须重启并重新验证全部 SHA，不能
靠替换磁盘文件改变正在运行的实例。代码、模型、虚拟环境仍须 root 管理只读。
服务拒绝现存 socket 路径，不盲目 unlink；systemd 的独立 RuntimeDirectory 在
服务停止后清理。非 systemd 手动运行若留下旧 socket，管理员应先确认原服务和
子进程已停止，确认该对象确为受控目录中的旧 socket 后再移除，不删除锁文件。
两模式均没有客户端身份体系，受保护同账号 socket 仅给可信 worker/查询适配层；
不得直接交给用户或模型。编码前后 C++ 的最新批准/版本/权限检查保持不变。

### 现有逐请求服务复现

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
