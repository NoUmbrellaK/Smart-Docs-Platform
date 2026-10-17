# Smart Docs Platform

面向小型项目团队的文档管理与智能整理平台。项目计划以 C++ 文件服务为业务核心，通过 Python MCP/Agent 服务提供受控的资料检索、交接核查和智能归档能力。

> M1 文件业务已于 2026-10-17 在当前 ECS 的资源限额内完成验收并审阅证据：登录会话、项目角色、目录、分片续传、不可变版本、受保护下载/PDF 翻页、文件状态操作和同源 Web 工作台。M1 被测干净提交为 `ed14dcf6029e53ac7106847fa456b3067af0147c`，M1 交接提交仅更新文档和归档证据。用户随后批准启动 M2 并选择方案 A：当前已实现 PDF/TXT/Markdown 本地解析、持久任务、可信本机 worker、事务式关键词索引发布及固定版本来源读取。需单独部署启用 worker；搜索业务/向量混合检索及 M3–M5 仍未完成。详细边界见[需求文档](./团队文档管理与智能整理平台_需求文档.md)。

## 当前实现

当前 C++ 服务提供：

- Linux `epoll` I/O 多路复用；
- HTTP/1.1 边界校验、路由、流式请求体和文件响应；
- PBKDF2 密码、服务端会话和同源 Cookie 安全边界；
- 项目级 `admin/editor/reader` 权限、成员和逻辑目录；
- MySQL 版本化迁移、RAII 连接池、事务与审计记录；
- 分片上传、断点恢复、幂等完成和启动核对；
- 不可变文件版本、带创建者/UTC 更新时间的文件元数据、重命名/移动、软删除/还原和远程 AI 授权标记；
- 鉴权下载、单段 Range、PDF 内联预览和同源 M1 Web 工作台。

网络服务器基线来自 [`markparticle/WebServer`](https://github.com/markparticle/WebServer) 的源码快照，固定于提交 `3b375204a80cc0de1caf5c076e95d6b5567a8159`；上述 M1 业务是在本仓库中实现的。许可证和来源信息见“开源来源与许可证”。

M1 的[单机部署手册](./docs/deployment/m1-single-host.md)、[HTTP API](./docs/api/m1-http-api.md)、[验收说明](./docs/evidence/m1/README.md)和[资源耗尽事故记录](./docs/operations/2026-09-15-m1-acceptance-resource-exhaustion.md)均在仓库内维护。

### M2 当前增量：本地解析、持久任务与 worker 闭环

已新增 [TXT/Markdown 解析函数](./python/smartdocs/text_parser.py)，仅依赖 Python 3.10+ 标准库：严格 UTF-8、BOM/换行规范化、有界分块、固定文件/版本 ID、原始内容哈希、稳定片段 ID 和行号/字符区间。文档中的指令保留为文本，不执行。不读取 API key、不访问网络、不读取任意文件路径。

默认解析上限为 8 MiB 输入、2,000,000 个字符、每片段 1,200 个字符、最多 2,000 个片段；超限或乱码明确失败，不返回静默截断结果。偏移是规范化文本的 Unicode 码点位置 `[start, end)`，行号从 1 起，不是原始字节或浏览器 UTF-16 偏移。调用方须在读取文件前鉴权并限制读取大小；当前本机 worker 通过下述 C++ 受控内容接口执行。

新增 [PDF 解析入口](./python/smartdocs/pdf_parser.py)：pypdf 6.21.0 在独立子进程中提取文字，片段绑定原始 PDF 哈希、固定版本及从 1 起的物理页码，字符/行号按页计数。最多 300 页，单页内容及嵌套 Form 内容流解压后的合计最多 16 MiB（同一 Form 只计一次）；子进程虚拟内存最多 256 MiB、CPU 时间 10 秒、墙钟默认 15 秒、输出文件 16 MiB，继承到的更紧限制不会被放宽。超时会终止并回收子进程，不返回部分成功。

全部无文本时明确失败；部分页无文本时返回 `pages_without_text`，这些可能是空白页或扫描页，不能宣称全页覆盖。没有 OCR，不接收密码；空密码可读取的加密 PDF 可以提取，其余明确拒绝。依赖缺失、损坏、超限和异常退出均返回不带原文的错误码。pypdf 提取警告（例如嵌套对象缺失或循环）也按失败处理，不把库返回的部分文本当完整结果；这是一项保守策略，可能拒绝部分可被阅读器容错打开的 PDF。子进程清空环境，资源限额不等于文件/网络安全沙箱；worker 必须非 root 运行、由 C++ 鉴权。

PDF 依赖安装到仓库外虚拟环境，按 [requirements](./python/requirements.txt) 校验 wheel 哈希，不安装可选 OCR/加密组件：

```bash
python3 -m venv /path/to/smartdocs-venv
/path/to/smartdocs-venv/bin/python -m pip install --require-hashes --only-binary=:all: -r python/requirements.txt
```

当前服务器 Python 3.10 缺少 ensurepip，本次没有修改系统软件包，而是使用 `python3 -m venv --without-pip`，再用现有 pip 的 `--target /path/to/smartdocs-venv/lib/python3.10/site-packages` 将同一锁定依赖装入该环境。其他 Python 版本需使用对应的 site-packages 路径，不要安装到系统环境。

解析器增量的小夹具测试 **28/28** 通过（文本 13、PDF 15），包含真实 PDF 提取、嵌套流/不完整提取拒绝、超时回收和子进程 300 MiB 分配被 256 MiB 内存限制拒绝；不代表最大规模性能验收。下述命令现在也包含 worker 测试，需允许本机回环 socket（在仓库根目录运行）：

```bash
(
  ulimit -v 524288
  ulimit -t 60
  PYTHONPATH=python timeout 90s /path/to/smartdocs-venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
)
```

只测试 TXT/Markdown 时，可用系统 Python 运行 `PYTHONPATH=python python3 -B -m unittest discover -s test/python -p 'test_text_parser.py' -v`，无需 PDF 依赖。

新增 C++ [持久任务接口与迁移说明](./docs/api/m2-processing-api.md)：reader 查询，editor/admin 取消和人工重试；pending 上限 100、running 上限 1，队列满仍保存上传文件；重启将运行中任务标记 interrupted，旧 attempt 不能回写新尝试。任务状态接口已复用项目权限和同源检查。

该增量在当前 ECS 受限单元中通过完整 C++ 回归 **100/100**（0 失败、0 跳过）及真实 schema 2→3 升级/重放检查；没有迁移原有业务库。测试范围、源哈希及并发覆盖缺口见[任务验证记录](./docs/superpowers/plans/2026-10-17-m2-processing-jobs.md#2026-10-17-验证结果)。

后续结果发布增量新增 C++ 有界结果校验、不可变片段持久化、事务式本地 FULLTEXT 发布及鉴权的固定版本来源读取；解析、关键词和向量状态分开，内部资料不请求远程向量化。详见[发布计划与证据](./docs/superpowers/plans/2026-10-17-m2-result-publication.md)和[来源 API](./docs/api/m2-processing-api.md)。

该发布增量完整 C++ 回归 **112/112**；审阅后仅补强测试，同一生产代码再通过结果校验 **7/7**、任务与来源 **19/19**、103 文件升级夹具；Python 解析回归 **28/28**。定向复验与全量回归存在重叠，不累计成更大的完整验收数字。

新增 [本机 Python worker](./python/smartdocs/worker.py)：受独立 bearer 保护的 C++ 回环接口领取任务、流式读取受控版本、执行真实解析并原子发布结果。单 worker 锁先于恢复，重启后的 interrupted 等待人工重试；取消/新版覆盖阻止旧 attempt 发布。仅 publish/fail 的传输失败或 5xx 最多原样重放一次，不把响应丢失当作事务失败。普通用户 JSON 限额不变，worker 结果最多 16 MiB。

本次完整 C++ 回归 **121/121**（0 失败、0 跳过）、Python **39/39**、隔离数据库真实 HTTP **7/7** 通过；包括三种格式、中文 FULLTEXT、60 万字符/500 块结果、损坏/不支持文件仍可下载、取消/新版冲突、单实例锁、C++ 重启与人工重试。使用当前 ECS 的非 root 受限单元串行验证，未迁移原有业务库。部署配置及复现命令见 [worker 手册](./docs/deployment/m2-local-worker.md)，完整构建/回归证据见 [worker 计划](./docs/superpowers/plans/2026-10-17-m2-local-worker.md)。

本机 worker 默认关闭，尚未安装为生产常驻服务；搜索业务接口及向量混合检索仍未实现，因此 M2 尚未完成。这不是大文件性能验收，也未重跑 M1 全套浏览器验收。没有模型请求或 DeepSeek token 消耗。详见[方案 A 设计](./docs/superpowers/specs/2026-10-17-m2-document-processing-design.md)、[文本增量](./docs/superpowers/plans/2026-10-17-m2-text-parser.md)、[PDF 增量](./docs/superpowers/plans/2026-10-17-m2-pdf-parser.md)与[任务增量计划](./docs/superpowers/plans/2026-10-17-m2-processing-jobs.md)。DeepSeek 聊天凭据不能直接作为向量能力已验证的证据。

## 规划架构

```mermaid
flowchart TB
    UI[Web 页面] --> CPP[C++ 文件与权限服务]
    UI --> AG[Python Agent 服务]
    AG <--> MCP[Python MCP 工具服务]
    MCP --> CPP
    CPP --> META[元数据与任务记录]
    CPP --> FILE[文件版本内容]
    CPP --> JOB[后台文档处理]
    JOB --> INDEX[关键词与向量索引]
    AG --> API[远程模型 API]
```

规划中的职责边界：

- **C++ 服务**：文件传输、版本、权限、幂等操作和最终写入检查；
- **Python MCP/Agent**：文档处理、受控工具接入和多步任务编排；
- **Web 页面**：文件工作台、上传任务、检索、Agent、归档确认和交接报告；
- **远程模型**：仅处理管理员明确允许远程 AI 使用的版本与片段。

## 仓库结构

```text
.
├── code/                 C++ WebServer 源码
├── python/smartdocs/     M2 本地解析与可信本机 worker
├── config/               无密钥环境变量模板
├── db/migrations/        MySQL schema 迁移
├── resources/            M1 同源 Web 工作台
├── scripts/              迁移和隔离测试脚本
├── test/                 单元、集成及 M1 验收场景
├── docs/api/             HTTP API 文档
├── docs/deployment/      部署与运维文档
├── docs/evidence/        可复现验收说明和阶段证据
├── docs/operations/      生产/验收事故记录
├── webbench-1.5/         上游压力测试工具
├── docs/superpowers/     设计与实施计划
├── Makefile              根构建入口
├── LICENSE               Apache License 2.0
└── 团队文档管理与智能整理平台_需求文档.md
```

## 构建与运行

### 环境

- Linux
- 支持 C++14 的 GCC
- GNU Make
- MySQL 8 Server 与 MySQL Client 开发库
- OpenSSL 开发库
- Python 3（迁移辅助与测试）；M2 解析模块要求 Python 3.10+

Ubuntu/Debian 可安装基础依赖：

```bash
sudo apt update
sudo apt install build-essential libmysqlclient-dev libssl-dev mysql-server mysql-client
```

### 配置与数据库

服务器只从环境变量读取运行配置，不需要修改源码。复制 [`config/smart-docs.env.example`](./config/smart-docs.env.example) 到仓库外的受保护位置，填入独立 MySQL 数据库/用户和绝对存储根，然后执行：

```bash
set -a
. /path/to/protected/smart-docs.env
set +a
scripts/migrate.sh
```

迁移成功输出 `schema_version=4`。升级前停止应用并备份数据库及文件，先阅读 [M2 迁移说明](./docs/api/m2-processing-api.md)。目录权限、systemd、HTTPS 和备份基线见 [M1 单机部署手册](./docs/deployment/m1-single-host.md)（其中 schema 2 为历史基线）。

### 编译

```bash
make -j1 server admin
```

生成 `bin/server` 和 `bin/smartdocs-admin`。小规格主机从单 job 构建开始，不要盲目提高并发。

### 运行

```bash
./bin/server
```

服务器启动前验证 MySQL、schema 版本、受控存储权限并执行上传状态核对；失败时不会以部分可用状态监听。浏览器入口为 `/app.html`。

### 测试与验收

普通测试目标：

```bash
make -j1 test
```

完整 M1 验收会执行清理构建、隔离 MySQL、真实 TCP/浏览器场景和二十轮进程故障恢复；验收主机还需提供匹配版本的 Chrome/Chromium 与 ChromeDriver，可用 `SMARTDOCS_BROWSER_BIN`、`SMARTDOCS_WEBDRIVER_BIN` 指定：

```bash
test/e2e/run_m1.sh
```

该命令资源消耗较高，默认单 job 并拒绝超过 `min(在线 CPU 数, 2)` 的构建并行度。同一时间只运行一个实例；受限服务器先阅读[事故记录](./docs/operations/2026-09-15-m1-acceptance-resource-exhaustion.md)。

2026-10-17 16:11:47–16:25:29（北京时间），按[同机受限验收方案](./docs/operations/2026-10-17-m1-same-host-limited-run.md)完成整轮验收：独立干净代码副本、非 root 浏览器沙箱、整个测试组 1600 MiB 内存上限、单核 CPU 配额、磁盘读写各 5 MB/s 与 200 IOPS。未扩容、未增加 swap，原 MySQL 服务保持运行。

- C++ 单元／集成测试 **90/90**，UI 契约 **8/8**，UI 行为 **18/18**；失败和跳过均为零；驱动自测 **26/26**。
- HTTP **9/9** 场景、44 项断言；真实浏览器 **2/2** 场景、20 项断言，涵盖刷新续传、错文件零 PUT、仅补缺失分片及 PDF 两页渲染。
- 四路各 **32 MiB**、**8 MiB** 分片；上传、合并、下载均实测四路重叠且最终哈希一致。服务峰值 RSS 增长 **1,515,520 bytes（约 1.45 MiB）**，严格小于 64 MiB 门槛。
- 中断恢复 **20/20** 轮、305 项断言，六类故障分布 **4/4/4/3/3/2**；无重复版本或半发布内容。

完整[验收汇总](./docs/evidence/m1/verified/summary.md)、[机器结果](./docs/evidence/m1/verified/results.json)、[环境](./docs/evidence/m1/verified/environment.txt)和两张已人工审阅的 [PDF 第 1 页](./docs/evidence/m1/verified/browser-artifacts/pdf-page-1.png)／[第 2 页](./docs/evidence/m1/verified/browser-artifacts/pdf-page-2.png)已归档。需求映射及 M2+ 排除项见 [M1 gate matrix](./docs/evidence/m1/gate-matrix.md)。此次验收不代表生产服务、TLS 或备份恢复演练已经部署完成，也不是吞吐量或长期稳定性承诺。当前 ECS 后续完整验收仍须使用资源限额，不要直接运行未受限命令。

## 开发路线

1. **M1 文件业务**：实现、完整受限验收和证据审阅已完成，阶段关闭；
2. **M2 文档检索**：进行中，已实现离线解析、持久任务、片段/本地索引事务发布和固定版本来源读取；worker 通道、搜索业务及混合检索待实现；
3. **M3 MCP 与 Agent**：实现五项业务工具和可观察的多步调用；
4. **M4 归档与交接**：方案确认、事务执行、交接清单和带引用报告；
5. **M5 验收与展示**：固定任务、故障测试、部署文档和可复现实验记录。

M1 已关闭，M2 目前仅实现上述解析及任务增量，尚未形成端到端检索闭环，M3–M5 仍是计划。阶段验收以需求文档和与目标提交绑定的实际测试证据为准。

## 安全说明

- API 密钥和真实数据库密码只放在仓库外的受保护文件中；不要提交 Cookie、证据临时目录、真实企业文件或其他敏感数据。
- 上传内容不能通过公开静态目录绕过权限检查。
- Agent 的写操作必须由服务端验证真实用户确认，模型输出不能代替授权。

## 开源来源与许可证

C++ WebServer 基线来源：[`markparticle/WebServer`](https://github.com/markparticle/WebServer)，上游提交 `3b375204a80cc0de1caf5c076e95d6b5567a8159`。

上游代码依照 Apache License 2.0 发布。本仓库保留其 [`LICENSE`](./LICENSE)；后续修改继承文件时应保留适用的版权和归属声明，并按许可证要求标示修改。本项目并非上游作者提供或背书的官方发行版。
