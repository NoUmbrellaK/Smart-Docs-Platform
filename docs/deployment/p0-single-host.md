# P0 单机部署入口（schema16）

核对基线`2bbe48e`，2026-10-21。本文把已有组件手册接成同一套部署流程，
不另造安装器或服务管理系统。**当前仓库具备M1–M4功能证据，但没有完成这套
生产部署、TLS验收和完整真实模型演示；M5/P0仍未验收。**
以下路径/域名是部署约定，不代表本机已存在或获得启用许可。
实际启用、停业务服务、迁移业务库及收费测试须另行安排，不能把阅读手册当作授权。

## 1. 版本与组件

部署目录约定`/opt/smart-docs`，非root运行；代码、模型、虚拟环境由管理员管理且运行账号不可写。
固定Git提交，同时记录二进制SHA-256、依赖锁文件、前端resources和实际配置的非敏感摘要。
不能将主工作区旧bin或故障注入版当作当前发布版。

| 组件/约定服务名 | 入口与依赖 | 网络与身份 | 完整模板 |
| --- | --- | --- | --- |
| `smart-docs.service` | `bin/server`，MySQL 8.0，schema16；`bin/smartdocs-admin`仅管理命令 | smartdocs账号；127.0.0.1:1316；独占storage及DB凭据 | [M1手册](m1-single-host.md)，用本文schema与配置覆盖历史值 |
| `smartdocs-worker.service` | `smartdocs.worker`，parser venv | smartdocs-worker账号；仅本机C++；独立worker token | [解析worker](m2-local-worker.md) |
| `smartdocs-vector.service` | `smartdocs.vector_worker`，parser venv及编码socket | 同一worker账号；本机C++及Unix socket；独立于解析的锁路径 | [向量调度](m2-vector-worker.md) |
| `smartdocs-encoder.socket` + `smartdocs-encoder@.service` | 逐请求编码，parser监督层 + embedding venv + 固定模型 | worker账号；0600 Unix socket；禁IP；不接收任何凭据 | [编码模板](m2-vector-worker.md) |
| `smartdocs-search.service` | `smartdocs.search_http`，parser venv | worker账号；127.0.0.1:1317；仅转发用户会话，无worker/DB/API凭据 | [检索网关](m2-hybrid-search.md) |
| `smartdocs-agent.service` | `smartdocs.agent_http`，MCP venv；每次任务创建私有stdio MCP子进程 | worker账号；127.0.0.1:1318；仅独立DeepSeek key文件，可选编码socket | [Agent网关](../api/m3-agent-http.md) |
| HTTPS反向代理 | 成熟代理终止TLS，保留Host/Origin，按下表分流 | 唯一公网入口；不公开内部接口或storage | 上述M1/检索/Agent手册 |

MCP目前是stdio，不需要另起匿名HTTP MCP服务器。归档确认/执行、报告保存与读取的
权威状态在C++；Agent网关只执行pending任务，不把waiting状态自动续跑。
如选已实现的`smartdocs-encoder-resident.service`，按向量手册的替换步骤修改全部消费者
socket/依赖，验证READY；不能与逐请求模式同时启动。本入口不默默切换编码模式。

三个Python环境独立安装锁定wheel，不合并requirements（parser与MCP存在不同版本约束）：

```sh
cd /opt/smart-docs
make -j1 server admin
python3 -m venv /opt/smart-docs-venv
/opt/smart-docs-venv/bin/pip install --only-binary=:all: --require-hashes -r python/requirements.txt
python3 -m venv /opt/smart-docs-mcp-venv
/opt/smart-docs-mcp-venv/bin/pip install --only-binary=:all: --require-hashes -r python/requirements-mcp.txt
python3 -m venv /opt/smart-docs-embedding-venv
/opt/smart-docs-embedding-venv/bin/pip install --only-binary=:all: --require-hashes -r python/requirements-embedding.txt
```

这些是新部署准备命令，不在既有venv上盲目覆盖；本轮未执行安装/编译。
已验证环境是Linux x86_64/CPython3.10，换平台需另验wheel兼容。
模型须提前准备并核对[embeddings.py](../../python/smartdocs/embeddings.py)中的五文件SHA、
库版本与编码修订；运行时禁止下载。Node/Chrome是测试依赖，不是生产网页的运行依赖。

## 2. 配置和凭据不能混用

先按M1手册建立账号、独立数据库、0700存储根及objects/staging。复制
[环境模板](../../config/smart-docs.env.example)后，**必须覆盖其开发默认值**：

```dotenv
SMARTDOCS_LISTEN_ADDRESS=127.0.0.1
SMARTDOCS_PORT=1316
SMARTDOCS_THREAD_COUNT=2
SMARTDOCS_MYSQL_POOL_SIZE=4
SMARTDOCS_STORAGE_ROOT=/var/lib/smart-docs
SMARTDOCS_SECURE_COOKIE=true
```

保留并填写模板中DB地址/库名/用户名/密码、上传/JSON/会话配置；
把`change-me`替换为仓库外随机密码。不能只复制上面六行就当作完整环境文件。
`SMARTDOCS_WORKER_TOKEN`默认空会关闭内部处理接口；启用解析/向量时由管理员生成
独立64位小写十六进制随机凭据，并同步到C++和worker两端。不得复用API key。
禁止`SMARTDOCS_FAULT_POINT`。公网必须HTTPS及Secure Cookie；HTTP例外仅用于隔离环回测试。

| 配置/路径 | 内容与访问边界 |
| --- | --- |
| `/etc/smart-docs/smart-docs.env` | C++的DB及worker token；root可写、smartdocs可读，不给网关/encoder |
| `/etc/smart-docs/worker.env` | 仅worker token、后端端口、锁路径、PYTHONPATH；由root管理，经systemd加载，不含DB密码/API key |
| `/etc/smart-docs/deepseek.env` | 仅`DEEPSEEK_API_KEY`，root管理0600，只有Agent单元加载；不是把用户已有key自动复制到此处 |
| 网关普通Environment配置 | `PYTHONPATH=/opt/smart-docs/python`、精确`SMARTDOCS_PUBLIC_ORIGIN=https://docs.example.com`及可选编码socket；不加载整份C++环境 |
| `/run/smartdocs-worker`、`/run/smartdocs-vector` | worker账号0700，解析和向量分别使用各自`worker.lock`；不能删锁绕开单实例 |
| `/run/smartdocs-encoder` | worker账号0700，socket0600；全部消费者使用相同绝对socket路径 |
| `/run/smartdocs-agent` | 0700临时会话context；由RuntimeDirectory管理，停止后不保留 |
| `/var/lib/smartdocs-agent-audit` | 0700持久审计、文件0600；监测空间、受控备份，不自动删除 |

凭据仅由服务管理器/受保护标准输入加载，不放命令参数、浏览器、文档、Git或截图。
不要用`env`、`set -x`或完整HTTP头诊断。worker/encoder运行账号不能读取业务storage；
读取资料由C++重新鉴权。共享服务账号的私有socket属于可信组件边界，不是多租户沙箱。

## 3. 迁移、启动和路由

当前server要求schema16；`scripts/migrate.sh`按仓库中001–016迁移执行。
M1手册里的`schema_version=2`仅是历史基线，M2的schema5也不是当前启动目标。
管理员升级前阻止新业务接入，停止所有写入方并确认退出：Agent网关（等待在途收尾）、
搜索网关、两个worker、编码socket及所有活动实例，最后C++。MySQL保留运行供备份/迁移。
只能对已确认的服务名执行停止；不能用宽泛进程匹配杀其他业务。

先做同一停写窗口的数据库、完整storage、持久审计及配置加密备份，
记录旧提交与文件哈希，再按[M1受保护加载方式](m1-single-host.md)显式执行：

```sh
sudo -u smartdocs bash -c \
  'set -a; . /etc/smart-docs/smart-docs.env; set +a; /opt/smart-docs/scripts/migrate.sh'
```

当前匹配版本成功输出应为`schema_version=16`；不要把shell可source配置文件交给不可信用户修改。
DDL不是整批事务，失败停止部署并从成对备份恢复；不能只退二进制、
手改schema版本号或自动重放半套迁移。详细规则见[schema16说明](../api/m3-tool-audit.md#schema16和部署)。

按完整模板准备unit并核对路径/账号/权限后，先验证unit和代理配置；这不等于启动成功。
不要直接拼接多个ExecStart或把示例差异片段当完整unit。
启动顺序：MySQL可用 → 单个C++实例 → `health/ready`返回200 → 编码入口 →
解析/向量worker → 搜索网关 → Agent网关（需已批准的key/外发范围/费用）→ 开放HTTPS入口。
`After=`仅表示排序，不保证HTTP/模型就绪；逐请求编码socket active也不证明模型已成功编码。
没有模型授权时保留Agent网关关闭，不用假key做真实任务，也不声称完整AI能力已部署。

| 公网路由 | 去向 | 关键限制 |
| --- | --- | --- |
| `/internal/`前缀 | 代理直接404 | `location ^~ /internal/`，不转发内部worker通道 |
| `/api/v1/projects/{32hex}/hybrid-search` | 127.0.0.1:1317 | POST；4KiB，缓冲开启，限流/连接上限，精确Host/Origin |
| `/api/v1/projects/{32hex}/agent-tasks/{32hex}/execute` | 127.0.0.1:1318 | POST `{}`；4KiB，不自动重试上游，不把HTTP200当业务成功 |
| 其他业务API、静态resources | 127.0.0.1:1316 | 保留公开Host；上传流式转发；请求上限大于8MiB分片 |

完整正则及超时分别用检索/Agent手册原模板；不能用`^~ /api/`或`^~ /`遮住分流规则。
未知Host拒绝，所有网关origin必须一致（含非默认端口、无尾斜杠）。
禁止把storage或审计目录映射成静态站点；禁止访问日志记录会话、原文或请求体。
页面创建任务、查状态、取消、确认修订、执行已确认归档仍走C++，不是所有agent-tasks都代理1318。

## 4. 资源和恢复边界

沿用[资源事故记录](../operations/2026-09-15-m1-acceptance-resource-exhaustion.md)及
[处理slice模板](m2-vector-worker.md)：所有处理组件共用CPU100%、MemoryHigh1200MiB、
MemoryMax1600MiB、Swap0及磁盘读写各5MB/s/200IOPS；实际磁盘设备须核对。
C++/MySQL/代理及OS仍需单独预算，不能把1600MiB当作整机峰值，也不能把各unit上限相加
当可用内存。不要并行构建、重型测试、浏览器及模型加载。监控可用内存、
memory.events、磁盘延迟/容量及审计增长，失效先停止自有操作，不提高配额掩盖争用。

encoder必须禁IP；worker/search仅需环回和Unix socket；Agent需要固定供应商HTTPS及DNS，
不能给Agent套只允许localhost的网络策略，也不能因此让encoder出网。
当前适配器固定模型与URL以[deepseek.py](../../python/smartdocs/deepseek.py)为准；
请求次数与token上限不是精确费用上限，报告/归档最高8192输出token，不能复用6×512烟测许可。

停机按上述反序依赖原则，确认所有子进程退出后才能备份或启动替代实例。
只停encoder socket不保证活动实例停止。只重启Agent网关不恢复C++ running任务：
查询持久任务、方案/报告/回执，再显式取消或由用户新建，不自动重放未知写入。
worker重启将遗留running标interrupted，需手动retry；不自动恢复整段模型会话。
归档计划修改后须确认新修订；恢复服务不产生确认，也不自动执行归档。
备份恢复必须使用新库、新storage、环回端口演练，见[配对恢复记录](../evidence/m3/vector-restore/README.md)。

## 5. 发布检查与证据

每次记录日期、提交、二进制/前端/依赖指纹、schema、脱敏配置、环境和结果；失败项也保留。

1. `systemd-analyze verify`检查已准备unit，`nginx -t`检查实际完整TLS配置；
   再核对127.0.0.1监听、socket权限、实际cgroup额度，不能仅检查模板文本。
2. C++ `GET /api/v1/health/live`及`/api/v1/health/ready`均200；
   HTTPS登录的Cookie/Origin匹配；公网`/internal/`404；未登录API被拒绝。
3. 人工资料上传后真实worker完成解析，固定版本可下载/定位；内部资料普通管理可用，
   但不得准入AI。新版默认未批准，默认检索不混入旧正文。
4. 实际编码及Hybrid查询、权限撤销后重查、停编码后明确关键词降级；
   关键词可用不等于Hybrid已验收。不要用假向量填通过。
5. 经新的限定授权后验证网页创建/执行任务、工具轨迹、报告引用；随后独立生成归档方案，
   真人确认具体修订后点击执行，核对回执与稳定版本。不得用模拟确认当人工演示。
6. 关停/重启与配对恢复核对真实状态、历史引用和未完成上传；保存截图/日志时脱敏。

现有固定评测入口见[单案例CLI](../evidence/m5/case-cli/README.md)，评测临时库不是生产安装器。
T-01–T-20及30项业务任务统计见[验收索引](../evidence/m5/acceptance-index.md)。
本手册的源码/CLI核对记录见[部署接线核对](../evidence/m5/deployment-map/README.md)；
没有Nginx/TLS、整套常驻服务与真实模型组合的运行证据时，上述清单不能标为完成。

后续[隔离HTTPS接线验证](../evidence/m5/tls-proxy/README.md)已运行实际Nginx与当前C++/临时SQL，
包含自签证书校验、登录Cookie/Origin、分片上传、下载/Range及内部路由遮蔽。
搜索/Agent两端只用本地接收器确认分流；不替代真实网关、编码、外部域名/CA、
systemd整体启动或生产上线验证。该测试不安装代理服务，不读取已有DeepSeek key。

随后[HTTPS真实网关组合](../evidence/m5/tls-gateways/README.md)接入真实搜索/Agent服务类、
stdio MCP及解析worker，单次1/1场景通过；固定引用、重复执行与撤销批准拦截已有证据。
仅模型HTTP transport为本地替代，实际encoder缺席并验证关键词降级；
仍不代表各CLI常驻安装、真实Hybrid、真实模型、归档确认或完整部署演示通过。

后续[真实离线编码接入HTTPS](../evidence/m5/tls-hybrid/README.md)已补真实Hybrid链路，
并验证Agent的限定版本及新版/撤权排除；最终2/2方法通过，首轮测试夹具失败保留。
编码器使用实际CLI及Type=notify临时单元，其余网关仍为服务类夹具；
收费模型仍本地替代，未完成整套常驻安装、真实模型业务评测或真人确认归档演示。

后续[systemd离线校验](../evidence/m5/systemd-verify/README.md)覆盖逐请求8个和常驻7个unit，
仅替换ExecStart可执行路径后两套通过，缺失依赖和未知选项负向对照有效。
原`/opt`路径仍未部署，宿主已有警告保留；未安装、reload或启动任何服务。
这只补充上述第1项的静态配置检查，不替代实际环境文件、账号权限、模块、模型、
socket、沙箱及启动就绪核验，也不将整项发布检查标为完成。
