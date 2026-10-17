# M2 关闭 CPU 内存池后的真实浏览器复验

被测提交 `7b50d2ba71a0ebd938178730222431da6f95f4d3`；北京时间
2026-10-17 23:56:27 至 2026-10-18 00:02:03。此次没有修改生产代码或测试断言。

## 结果：0/1，M2 仍未验收

隔离 MySQL schema 5 迁移与 103 文件升级夹具通过。真实组合用例运行 240.485 秒，
退出码 1：Chrome 已启动，但首次编码及唯一一次显式 API 重试均为持久
`embedding_timeout`；失败断言位于 `hybrid_browser_test.py:113`。没有到达混合搜索、
来源定位、版本更新或降级断言，没有成功截图。见[完整输出](check.txt)。

编码服务使用正式 `smartdocs.embedding_service`，每请求独立子进程；没有使用预热、
缓存模型或 fork 原型。新鲜服务在 23:56:13 启动，原总寿命上限 900 秒；测试结束时
仍 active，随后显式停止。两个连接分别于 23:59:18、00:00:37 建立，处理入口分别于
00:00:21、00:01:41 退出。入口退出码 0 仅表示完成协议响应，不表示编码成功；
业务失败由上述持久错误码证明。此次可排除总服务寿命到期作为失败原因。

共享组 memory.high 计数 36274→39710，max/oom/oom_kill 均为 0。运行中观察到
Chrome 和模型进程磁盘等待；未采集本轮完整编码内部阶段时序，不能把耗时精确归到
哈希、导入或推理。此前[内存对照](../encoder-memory/README.md)的约 84 MiB 节省
确有样例证据，但本次证明单独关闭内存池仍不足以通过当前组合场景；不是 OOM，
也不足以得出必须物理扩容的结论。

## 环境与复现

当前 ECS：2 vCPU、3495 MiB RAM、无 swap。测试和编码服务均为 nobody/nogroup，
共享 `smartdocsvectorcheck.slice`：MemoryHigh=1200MiB、MemoryMax=1600MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，/dev/vda 读写各 5MB/s、200IOPS，Nice=10。
编码服务单独 PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、
NoNewPrivileges=yes；测试服务需要回环 HTTP，不具备该禁网配置。

按[向量部署手册](../../../deployment/m2-vector-worker.md)配置上述限额与隔离，
在现有非 root 副本使用 `systemd-socket-activate --accept --inetd` 启动正式入口：

```text
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m smartdocs.embedding_service
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
```

该文本是分行参数说明，不是可直接复制执行的 shell。使用私有目录 0700，绑定新的
`arena-browser.sock`，设置 PYTHONPATH 指向副本 python。启动后观察到 socket 为
0644，故在任何客户端连接前显式 chmod 0600 并验证，没有放宽客户端权限检查。
编码服务 RuntimeMaxSec=900、TimeoutStopSec=5；仍保留请求内的原 60 秒截止期。
精确 ExecStart、环境和状态见[服务记录](unit-status.txt)。

测试单元 WorkingDirectory 为 `/tmp/smartdocs-safe.h4SzvX/repo`，
RuntimeMaxSec=780、TimeoutStopSec=10、KillMode=control-group、RemainAfterExit=yes。
设定服务记录中的 PYTHONPATH、socket、Chrome、driver、输出目录变量后运行：

```bash
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/hybrid_browser_test.py
```

不要脱离上述资源组在服务器上无限额运行。使用现有模型、依赖和 C++ 二进制，
没有下载、重新构建或读取 DeepSeek 配置；无远程 API 调用。相关 Python、资源、
测试和迁移脚本已与副本逐一 cmp；关键[源码哈希](source-sha256.txt)已记录。
本轮不重跑 Python/JS/C++ 单独套件，不挪用历史通过数量充当本轮成果。

收尾：测试 MainPID=0；新建编码服务停止后已回收，MainPID=0、inactive/dead；
原 mysql 仍 active。临时数据库由原脚本清理。driver.log 可能包含自动登录参数，
不归档，只保留无凭据的测试输出和编码服务连接日志。

## 后续边界

不再原样重跑或叠加重试；现有默认模型与浏览器同组运行的资源适配仍未解决。
下一步应先量化浏览器自身的进程与内存构成，评估能否在保持沙箱和全部断言的前提下
减少无关常驻负载，再决定最小改动。不能靠假模型、删断言、抬高配额或延长请求
时限宣布成功；更换模型属于需要单独验证兼容性与检索质量的选型，不在本轮实施。
测试专用同源代理不是生产 Nginx/TLS 部署；M3 尚未启动。
