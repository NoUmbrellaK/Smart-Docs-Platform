# M2 真实浏览器到混合检索模型链路

日期：2026-10-17；生产代码基线 `729d2b56c8451014417e9c5baaaba94a8de6bedd`。
本增量只增加验收测试与证据，不修改生产代码、数据库结构或模型配置。

## 实测结果：组合验收未通过

最终单元 `smartdocs-hybrid-browser-recovery` 于北京时间 22:45:55–22:51:28 执行，
退出码 1、MainPID=0。Python **74/74**、JS **37/37**、schema 5 与 103 文件升级夹具
通过；真实浏览器/模型组合测试 **0/1**，226.740 秒。Chrome 已启动，但首次模型
编码及一次显式人工重试均返回持久 `embedding_timeout`，未进入搜索/来源断言，
没有可归档的成功截图。最终输出见 [final-check.txt](final-check.txt)。

编码服务从 22:30:37 运行至 22:50:38，达到自身 20 分钟总寿命，Result=timeout、
MainPID=0。该寿命跨越前述多个串行验收单元，不能将最后失败纯粹归因于模型或 RAM；
先前第四轮在服务仍存活时已单独证实模型超时。后续需要在新鲜编码服务寿命内区分
冷加载、内存回收和磁盘节流的影响，不能据此断言必须物理扩容。

共享组 memory.high 计数本轮全过程 10601→19412，max/oom/oom_kill 始终为 0。
存在明显节流，不能宣称无内存压力。收尾确认无运行中的 smartdocs 临时服务，
生产 mysql 仍 active；退出和限额记录见 [unit-status.txt](unit-status.txt)。
相关 14 个源码文件与非 root 测试副本逐一 cmp，见 [源码哈希](source-sha256.txt)。
本测试未加入默认测试入口；保留为能暴露当前验收缺口的专用场景，不宣称全部测试通过。

## 测试覆盖设计（不是已通过证明）

[测试](../../../../test/e2e/hybrid_browser_test.py)使用非 root、启用沙箱的 Chrome，
经测试专用同源回环代理访问真实 Python SearchHTTPServer、C++、临时 MySQL 和禁网
本地编码服务。代理只转发实际响应，不生成假搜索结果；内部接口不经代理开放。
浏览器负责登录、查询和读取出处；上传、解析、批准及版本变更由真实 API/worker
准备，不能把这些准备步骤称为全部经过 UI 操作。

组合场景检查：

- 实际解析人工 TXT，只有批准的具体版本发布真实向量；内部文件没有向量任务。
  如果首次编码确认为 `embedding_timeout`，检查失败已持久化，再通过真实 API 显式
  重试一次并要求成功；其他错误或第二次失败直接使测试失败。日志明确报告此分支，
  不是生产自动重试策略，也不把一次重试后的成功称为冷启动成功。
- 中文“如何恢复备份？”以 vector 通道找到英文恢复资料；读取固定版本原文，
  校验行号与完整项目/文件/版本下载链接，并保存 360/1280px 截图。
- 将网关编码目标改为真实不存在的 Unix socket，检查显式
  `query_embedding_unavailable`、`keyword_only` 及相同批准范围，不泄露内部材料。
- 发布新版后，保留的旧引用仍读取原版本并标记历史来源；新查询排除旧版本。
  元数据模式能发现待解析新版；解析完成后关键词可用，但批准不继承。
  批准新版后同范围降级检索可见，撤销批准后消失。

## 运行与边界

全部在当前 ECS 的非 root 副本串行运行，不下载模型或依赖，不读取 DeepSeek env，
不调用远程 API。测试及编码服务共享 `smartdocsvectorcheck.slice`：MemoryHigh=1200MiB、
MemoryMax=1600MiB、SwapMax=0、CPUQuota=100%、TasksMax=256，/dev/vda 读写各
5MB/s、200IOPS；Nice=10。没有扩容、增加 swap 或提高模型 60 秒超时。
编码服务 PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes，
私有目录 0700、socket 0600，同一非 root 账号并复用模型锁。

复现须先按[向量部署手册](../../../deployment/m2-vector-worker.md)准备受限、禁网编码
服务，再在同一资源组内运行；以下不是可在生产机无限额直接执行的建议：

```bash
PYTHONPATH=python \
SMARTDOCS_ENCODER_SOCKET=/controlled/private/encoder.sock \
SMARTDOCS_BROWSER_BIN=/controlled/chrome \
SMARTDOCS_WEBDRIVER_BIN=/controlled/chromedriver \
SMARTDOCS_HYBRID_BROWSER_OUTPUT=/controlled/evidence \
scripts/test-with-mysql.sh -- /controlled/venv/bin/python -B test/e2e/hybrid_browser_test.py
```

测试代理不是生产 Nginx/TLS 部署。网关在测试进程内运行，不证明 CLI 入口的地址
空间限额；CLI 边界与真实子进程网关由[前轮证据](../hybrid-gateway/README.md)覆盖。
小型人工文本不证明搜索质量、吞吐量、任意冷启动耗时或长期稳定性。
没有重跑完整 C++/M1 套件；生产代码未变，完整 C++ 141/141 的证据仍对应上述基线。
本轮不是 M2 阶段关闭或完整 P0 完成，M3 尚未启动。

## 失败记录

1. 首轮在浏览器启动前失败：socket-activate 实际创建的 socket 为 0644，客户端按
   安全契约拒绝。将测试 socket 显式收紧到 0600；没有放宽生产权限检查。
2. 第二轮仍在浏览器启动前失败，模型阶段约 60 秒后返回 failed；未记录具体持久
   错误码，因此只能判定与既有冷加载超时表现一致，不能将推测写成已确认错误码。
   子编码进程已结束，无 OOM。补充失败断言的持久错误码后，保持原限额串行复验。
3. 第三轮真实向量发布通过，但 Chrome 会话创建失败，驱动报告
   `DevToolsActivePort file doesn't exist`，尚未登录。没有 OOM，不能将此错误直接
   归因为内存不足。随后将测试顺序改为先启动沙箱浏览器、再加载模型，以减少模型
   加载后立即冷启动 Chrome 的缓存竞争；不关闭沙箱，不提高资源或超时上限。
4. 第四轮 Chrome 会话已成功创建，但模型编码持久错误码明确为 `embedding_timeout`。
   没有 OOM；新增上述一次显式 API 重试验证，保持生产限额和失败语义不变。
5. 最终一轮仍失败，详见顶部实测结果；不再追加重试或测试负载。

原始输出：[socket 权限](failed-socket-mode.txt)、[模型加载](failed-model-loading.txt)、
[Chrome 启动](failed-browser-start.txt)、[明确超时码](failed-confirmed-timeout.txt)。
独立只读审查已核对清理、来源断言、批准范围与严格的一次重试门槛，无剩余静态
审查问题；代码审查通过不等于当前真实组合场景已经通过。

不把失败轮次的局部通过结果叠加为最终验收数字。浏览器驱动原始日志可能含自动化
登录参数，不归档；仅保留无凭据的测试输出、资源状态及截图。
