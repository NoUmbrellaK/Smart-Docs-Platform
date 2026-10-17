# M2 本机 Python worker

本手册描述可选的解析 worker 部署，不表示当前 ECS 已安装生产 worker。沿用 [M1 部署基线](m1-single-host.md)，当前数据库要求 schema 5（升级见 [处理 API](../api/m2-processing-api.md)）。[本地搜索 API](../api/m2-search-api.md)已接通；C++ 向量任务与分批发布已实现，但独立编码器尚未接入调度，混合检索与 Agent 未完成。不要将约 737 MiB RSS 的编码器加载到本手册限制 512 MiB 地址空间的解析 worker 内。

## 身份与凭据

- C++ 是权限、版本、任务和发布的唯一权威。worker 是可读取待处理内部资料的可信本地服务，不是用户或 Agent；本地解析不要求远程 AI 批准，但不能向外发送资料。
- C++ 和 worker 使用同一份**独立随机** 32 字节凭据，编码为 64 个小写十六进制字符。安全生成并分别写入仓库外受保护的环境文件，不打印到终端/日志、命令行或 Git；不要复用 DeepSeek key。本代码不读取 DeepSeek 环境文件。
- C++ 配置 `SMARTDOCS_LISTEN_ADDRESS=127.0.0.1` 和 `SMARTDOCS_WORKER_TOKEN`。为空时禁用全部内部路径；格式错误或非回环监听启动即拒绝。
- worker 使用独立非 root 系统用户（如 `smartdocs-worker`），CLI 拒绝 root。代码及虚拟环境只读，不授予 MySQL 密码或文件存储目录权限；内容只能从 C++ 领取。root 管理的 systemd EnvironmentFile 建议 0600，不能供普通用户读取。
- 只部署一个 worker，所有手动诊断和服务实例都用同一个用户与绝对锁路径；父目录为该用户独占、0700。锁文件不能删除/替换或用另一个路径规避。每个数据库只运行一个 C++ 实例。

worker 环境文件只需：

```ini
SMARTDOCS_WORKER_TOKEN=<与 C++ 相同的独立 64 位十六进制随机凭据>
SMARTDOCS_WORKER_PORT=1316
SMARTDOCS_WORKER_LOCK_PATH=/run/smartdocs-worker/worker.lock
PYTHONPATH=/opt/smart-docs/python
```

前端仍经 HTTPS 反向代理访问 C++。在现有 nginx server 块增加拒绝规则，避免通用代理把内部通道转发出去；发布前验证外网无法访问：

```nginx
location ^~ /internal/ { return 404; }
```

即使代理误配，bearer 认证仍必需；Origin/Cookie 拒绝不能替代保护凭据。不要把 worker token 放入浏览器、用户会话、URL 或应用报错。

## 运行与资源

按照 README 的哈希锁定 requirements 在独立 Python 3.10+ 虚拟环境安装 pypdf。worker 自身虚拟内存最多 512 MiB，继承更紧限制不会放宽；PDF 子进程另限 256 MiB、10 CPU 秒、15 秒默认墙钟、16 MiB 输出。子进程清空环境；这不等同于安全沙箱，仍需非 root 和 OS 级资源限制。

以下 systemd 模板需按实际路径、服务名及磁盘设备修改，**不要直接在生产环境照抄启用**：

```ini
[Unit]
Description=Smart Docs local document worker
After=smartdocs.service
Requires=smartdocs.service

[Service]
Type=simple
User=smartdocs-worker
Group=smartdocs-worker
WorkingDirectory=/opt/smart-docs
EnvironmentFile=/etc/smart-docs/worker.env
RuntimeDirectory=smartdocs-worker
RuntimeDirectoryMode=0700
UMask=0077
ExecStart=/opt/smart-docs-venv/bin/python -B -m smartdocs.worker
Restart=no
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
MemoryHigh=384M
MemoryMax=512M
MemorySwapMax=0
OOMPolicy=stop
CPUQuota=100%
Nice=10
TasksMax=32
IOReadBandwidthMax=/dev/vda 5M
IOWriteBandwidthMax=/dev/vda 5M
IOReadIOPSMax=/dev/vda 200
IOWriteIOPSMax=/dev/vda 200

[Install]
WantedBy=multi-user.target
```

先启动 C++ 并确认 readiness，再启动 worker；`After` 只保证进程顺序，不保证 HTTP 就绪。本例故意不自动重启：启动/连接失败时检查后人工启动，避免忙循环掩盖故障。主机总额度还要涵盖 C++/MySQL；这些值不是吞吐量保证。当前小 ECS 的构建/数据库/浏览器验收仍须串行使用原有整个测试组 1600 MiB、单核和 I/O 限制，不能将此单 worker 模板代替测试组限额。

停机/迁移顺序为 worker → C++；恢复为 C++ 就绪 → worker。轮换凭据也遵循此顺序，同时更新两端。不要在第一个实例仍在运行时启动另一个 C++ 执行恢复。

CLI `--once` 只执行一次领取，不保证排空整个队列；常驻模式空闲每 5 秒轮询。均先获取 flock，再将遗留 running 标记 interrupted，等待用户人工 retry。锁竞争时第二个进程退出，不能先 recover 打断首个进程。

## 故障与验收

worker 不重试领取：响应丢失时可能已领取，不能再抢另一个任务。发布/失败回报只有一次原样重放；两次失败后退出，不臆断数据库提交结果。查看用户任务状态，恢复后人工重试 failed/interrupted/cancelled 的当前版本。取消与新版覆盖阻止旧结果发布，但不即时终止已经运行的有界解析。损坏/不支持的文件仍受文件服务管理，可鉴权下载，不伪造索引成功。

安全日志只含状态/错误码；不输出原文、响应体或 token。不要通过打印环境或完整请求头排查凭据。固定版本来源读取仍需项目权限；内部 worker 通道不得用于绕过 Agent 的额外外发批准。

真实闭环回归（仅在受限测试单元里，虚拟环境必须包含锁定 PDF 依赖）：

```bash
PYTHONPATH=python scripts/test-with-mysql.sh -- /path/to/venv/bin/python -B test/e2e/m2_worker_test.py
```

脚本拒绝非隔离临时 MySQL socket；测试真实上传、三种格式解析、来源片段、中文 FULLTEXT、较大结果、取消/新版冲突、worker 锁与 C++ 重启、人工重试及认证。它不等同于 M2 搜索质量、最大文件性能、生产部署或 M1 全套浏览器验收。实际结果见 [本次计划与证据](../superpowers/plans/2026-10-17-m2-local-worker.md)。
