# M5 持续解析与搜索 CLI 实测

2026-10-21，基线 `8b5df03` 加[新测试](../../../../test/e2e/m5_processing_cli_test.py)。
补齐实际命令行启动的证据：使用 parser venv 分别执行
`python -B -m smartdocs.worker`（不带 `--once`）和 `smartdocs.search_http`，
真实连接 C++ 与隔离 SQL，不在测试进程内直接实例化服务类。
仅新增测试，无产品代码、依赖或部署模板变更。

## 实际结果

[完整日志与终态](verified.txt)：01:45:00—01:46:51（Asia/Shanghai），
单次 **1/1 场景通过**，0 失败/错误/跳过，测试本体 7.487 秒，
建库/迁移及清理另计；十条迁移 PASS 不加入场景分母。
三组检查：

1. 持续 worker 解析两份 TXT；同锁启动第二实例返回 1 和
   `worker_already_running`，原实例仍运行。首批完成后再上传第三份 TXT，
   同一进程继续完成解析，验证不是只执行一批就退出。
2. 空闲停止 worker 后上传 Markdown，确认任务 pending；新进程使用同一锁路径启动，
   完成排队任务，原文档解析状态保持 completed。
   这是空闲停止后的重启，不是处理中断或崩溃恢复测试。
3. 单独搜索 CLI 两次启动在同一端口：匿名请求 401，错误 Origin 403；
   已登录查询明确为 keyword_only，带 query_embedding_unavailable，
   只返回获批文件、不含内部内容。重启后片段与固定版本完全相同，
   受保护来源可读取；撤销批准后新查询为空，不沿用旧结果。

搜索进程只加载 PYTHONPATH、固定 origin 和不存在的 encoder socket，
不继承测试库或 worker 凭据；worker 只取得自己的 token、端口与锁路径。
这验证 CLI 配置接线，不是不同操作系统账号间的权限隔离证明。
所有子进程均有 finally 清理；若必须强杀则测试失败。日志检查不含 worker token。

## 环境与复现

[源码/二进制指纹及运行前资源状态](sources.txt)、
[迁移指纹、实际单元属性和 Python 版本](running.txt)。
相关 C++、Python、测试 helper、迁移逐字节核对测试副本；不宣称副本为完整 checkout。
未重新编译，server/admin 指纹与此前当前源码回归使用的二进制一致。
Python 3.10.12、隔离 MySQL 8.0.46；schema16，禁用数据库网络。
初始化的空 root 密码警告只属于一次性隔离库，不是业务 MySQL 配置。

在已有安全副本 `/tmp/smartdocs-safe.h4SzvX/repo` 下执行：

```sh
systemd-run --unit=smartdocs-m5-processing-cli \
  --property=User=nobody --property=Group=nogroup \
  --property=Slice=smartdocsvectorcheck.slice \
  --property=WorkingDirectory=/tmp/smartdocs-safe.h4SzvX/repo \
  --property=Nice=10 --property=UMask=0077 --property=NoNewPrivileges=yes \
  --property=KillMode=control-group --property=TimeoutStopSec=5 \
  --property=RuntimeMaxSec=480 \
  --property=IPAddressDeny=any --property=IPAddressAllow=localhost \
  --setenv=PYTHONPATH=python --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /bin/bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m5_processing_cli_test.py
```

共享 slice 为 CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
`/dev/vda` 读写各5MB/s及200IOPS；不能在未准备这些限制的环境照抄命令。
全部组件在一个受限临时单元内，不是生产模板中的独立 unit 安装。
解析和搜索各自 CLI 仍启用原有进程资源限制，不调整超时或上限。

终态 success/exit0、inactive/dead、MainPID=0、slice TasksCurrent=0，
无 nobody 残留进程，原 MySQL active；临时库和存储由夹具清理。
memory.high 保持82471，max/oom/oom_kill保持0，可用内存2040MiB。
不是峰值 RSS、吞吐或长期稳定性测量。

## 未覆盖

本轮未启动 vector worker、encoder 或 Agent；没有模型 key 读取或远程调用，
没有 TLS/浏览器、真实模型评测、真人确认或生产安装。
CLI 本机测试不是完整部署演示，三组断言不计入固定30项业务任务。
独立只读复核未发现阻断项；M5/P0 仍未完成。
