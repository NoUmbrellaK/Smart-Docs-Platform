# M2 正式串行分词与推理编码器

2026-10-18，修改基线 `7d5f18ec8a68a99e945fe2b5145222f289bbf37a`。
本增量将已验证原型接入正式编码入口；不安装生产服务，M2 仍待阶段综合验收。
被测源码由本目录 source-sha256.txt 绑定，执行副本与工作树一致。

## 实现边界

同一监督进程持有原共享锁，先运行分词子进程并回收，再启动推理子进程。
不是嵌套的新会话孙进程；两个阶段与最终结果校验共享原 60 秒总预算。
分词输出在 JSON 解析前限制为 256 KiB，推理输入亦限 256 KiB；外部输入仍限
64 KiB，最终输出仍限 2 MiB。中间结果校验身份、文本 SHA、修订、完整连续区间、
token ID 范围、mask、每窗 128 tokens、每批 128 窗。最终验证成功才返回结果。

分词阶段校验固定 JSON 文件，推理阶段重新校验全部固定文件（含 ONNX）。
仍要求 root 管理且服务账号只读的模型/代码目录；未声称解决可写模型目录的替换竞态。
沿用固定 FastEmbed 分词 helper、池化、归一化、依赖版本、模型及索引修订；
CPU 单线程、关闭 arena，没有新增依赖、常驻模型或 malloc_trim。

## 回归结果

- 首先新增两项阶段/截止期测试，旧实现得到 2 个有效失败，见 initial-red.txt。
- 审查发现分词输出在解析前缺少独立限额，新增测试先失败再修正，见 stage-limit-red.txt。
- 最终完整 Python 回归 **83/83，14.013 秒**，其中编码模块 **17 项**。
  覆盖共享锁/剩余预算、两阶段超时、真实子进程先退后起、KeyboardInterrupt 清理、
  错误中间数据、依赖缺失安全错误及输出限额。没有把 KeyboardInterrupt 测试
  当作任意硬杀监督进程或任意逃逸后代的通用回收证明。
- 非 root、禁止 IPv4/IPv6 的真实模型回归 **1/1，10 项检查通过**。
  两次新进程编码分别 4.562/5.872 秒，子进程峰值 RSS 501164 KiB（约 489 MiB）。
  这是本次样例进程指标，不是冷启动、整个 cgroup 或长期稳定性承诺。
- 兼容性用旧基线已有的 6 样例 / 16 窗完整输出 SHA 作断言：
  `6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`。
  基线见 ../serial-encoder-spike/baseline.txt；不是新实现自己生成期望值。
- 首次真实测试在模型启动前因 if_nameindex 被 AF_UNIX 白名单拒绝而失败；
  测试改为直接断言 IPv4/IPv6 socket 创建被拒绝，未放宽网络隔离。
  原失败和最终成功均保留在 regression-logs.txt。
- 独立代码审查提出的依赖错误映射、早期输出限额、正式兼容性断言均已修正，
  复审无 Critical/Important 项。未重跑无改动的完整 C++/前端回归。

## 环境和复现

当前 ECS，全部重型测试串行进入 smartdocsvectorcheck.slice：MemoryHigh=1200 MiB、
MemoryMax=1600 MiB、SwapMax=0、CPUQuota=100%、TasksMax=256，/dev/vda 读写各
5 MB/s、200 IOPS。User=nobody、Group=nogroup、Nice=10；原 MySQL 不改动。
模型测试 PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes、
UMask=0077、KillMode=control-group。整套模型测试单元 RuntimeMaxSec=240，
不改变每次编码请求的 60 秒总预算；完整 Python 单元上限 120 秒。

在相同受限单元中执行（不是无限额运行建议）：

```bash
PYTHONPATH=python /path/to/parser-venv/bin/python -B -m unittest discover -s test/python -v
/path/to/model-venv/bin/python -I -B test/e2e/m2_embeddings_test.py \
  /path/to/fixed-model /path/to/model-venv/bin/python
```

测试只用人工文本，未读取 DeepSeek 密钥或调用远程模型，不消耗其 token。

## 浏览器组合复验

使用未修改的 hybrid_browser_test.py 和 m1_browser_test.py，新鲜离线编码服务与
独立临时 MySQL 同组限额运行一次。保持 Chrome 沙箱、原断言、60 秒请求上限和
至多一次显式 API 重试不变；不调整浏览器顺序，不拼接不同运行的成功结果。
本轮 **0/1，322.157 秒**，单元退出码 1；失败不能计为 M2 验收通过。
完整日志见 [browser-combination.txt](browser-combination.txt)。

- 首次编码 embedding_timeout，失败正确持久化；唯一一次显式 API 重试后真实向量
  发布成功，未批准 internal 文档没有被向量化。
- 本次同一运行通过页面导航、登录、中文“如何恢复备份？”的纯向量英文命中，
  固定原文、版本、Lines 1–1 和下载 URL 校验。两张截图已人工查看：
  [360 宽](hybrid-real-360.png) / [1280 宽](hybrid-real-1280.png)。
  截图是搜索区域在当前视口内的截取，手机图不包含下方完整来源；来源正确性依据
  浏览器断言，不把截图描述成全页或独立的全部断言证据。
- 后续通过编码服务不可用时显式 keyword_only 降级、批准范围排除、旧来源显示
  historical version、新版元数据 pending、解析后未批准新版不进入 hybrid。
- 失败于 hybrid_browser_test.py:185：keyword 查询 Replacement 期望 1 条，实际 2 条。
  此后“新版出现在关键词结果且旧版排除”、新批准/撤销断言尚未完成。
  不可用此前独立测试或其他轮次的成功拼成这一轮通过。

只读复核发现 SQL 使用 ngram IN NATURAL LANGUAGE MODE，接口明确不是精确子串。
Replacement 与 internal 样例存在可能重叠的 bigram；keyword 模式本就允许检索
当前用户有权限的未批准本地文档，与 hybrid 批准范围不同。因此更可能是测试预期
错误，尚未单独实证具体命中评分。下一增量先用轻量关键词回归确认，再将这项测试
改为精确核对新版/旧版身份与可发现性，而非无依据改总数为 2 或修改生产搜索语义。

共享组 memory.high 从 47501 增至 52629（+5128），max/oom/oom_kill 始终为 0。
首次冷启动超时仍是实测限制，不能声称资源问题全部解决或长期稳定性已验证。
结束时编码服务仍在寿命内正常运行，随后显式停止；测试组已无运行单元，
原 MySQL active，主机 MemAvailable 恢复到 1898016 KiB。
[终态及限额](unit-status.txt)保留失败退出码，没有将 not-found/MainPID=0 当成功证据。
测试脚本按既有流程清除了本轮临时数据库；不会恢复其中人工测试数据。
没有提高资源限额、增加 swap、重排浏览器动作或消耗远程 token。
