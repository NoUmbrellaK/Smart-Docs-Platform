# M2 混合检索同源入口与工作台

日期：2026-10-17；基线 `d1c3d03b2c4ecc43f580b693fddceae59094bcec`。
复用既有查询协调器、私有编码服务、C++ 鉴权/检索和页面，不新增依赖或数据库迁移。
实现边界见 [接口](../../../api/m2-search-api.md#工作台混合检索网关)与
[部署模板](../../../deployment/m2-hybrid-search.md)。源码哈希见
[source-sha256.txt](source-sha256.txt)，已与非 root 测试副本逐文件 cmp 一致。

## 实测结果

- Python **74/74**（包含新增 HTTP 边界 10 项）：真实回环 HTTP，外部模型/C++ 在这组
  小测试中为明确的替身。覆盖固定目标/Origin/session-only 转发、两次鉴权、预检拒绝
  不编码、编码后失权拒绝、超限请求与响应、重复头/字段、非法 JSON、未知身份/向量/
  mode 参数拒绝、上游错误脱敏、两连接上限及第三连接 503。
- Node **27/27**：搜索交互 9 项（新增 2）和原 UI 行为 18 项。检查 Hybrid 专用路径与
  请求体、批准范围、真实 retrieval/关键词降级、向量覆盖和命中通道，以及既有固定
  出处、分页、错误、跨项目/条件变更和晚到响应隔离。
- 真实 C++ HTTP + 临时 MySQL + 离线模型 **4/4**，45.859 秒（不含建库/迁移）。
  原查询用例现通过实际非 root `python -m smartdocs.search_http` 子进程执行，包含
  C++ 有效/无效会话、中文查询英文恢复材料、vector 命中、固定版本引用、缺失私有
  编码 socket 时同范围关键词降级、撤销批准后结果消失。另三项保留向量 worker 的
  两批发布、锁/恢复/重试、撤销及重新批准验证。这不是四个全新的独立业务场景，
  也不是准确率或性能评测。
- schema 5 初始化、迁移重放和 103 文件升级夹具通过。无 C++ 生产源码变动，复用
  基线二进制；本轮没有重新编译或重跑完整 C++ 测试套件。
- 页面契约 **8/8**、浏览器工具自测 **12/12**；真实非 root、sandbox enabled 的 Chrome
  使用明确的本地 HTTP API 夹具，检查 metadata/keyword/hybrid、网关请求体、批准范围、
  命中通道、降级、分页、固定来源、拒绝读取、安全文本及变更失效。
  360/375/390/414/768/1280 六种视口均无搜索区横向溢出，交互目标不小于 44px。
  该浏览器夹具不调用 C++ 或模型，不能冒充“浏览器到真实模型”的完整端到端验收。

[联合测试输出](verified-check.txt)、[浏览器输出](browser-check.txt)、
[最终单元状态](unit-status.txt)随代码归档。
移动端与桌面截图分别为 [360px](search-360.png)、[1280px](search-1280.png)；
长页额外归档 result/source 区域截图，页面上的脚本样式字符串是安全文本测试资料。

## 复现与运行记录

在 `/tmp/smartdocs-safe.h4SzvX/repo` 非 root 副本串行运行；仅使用人工文本和现有缓存
模型，不下载依赖、不读取 DeepSeek env、不调用付费 API。先联合测试，停止编码服务
和临时 MySQL，再启动浏览器。共享 smartdocsvectorcheck.slice：MemoryHigh=1200M、
MemoryMax=1600M、SwapMax=0、CPUQuota=100%、TasksMax=256；/dev/vda 读写各
5 MB/s、200 IOPS，Nice=10。联合单元 RuntimeMaxSec=1800，浏览器单元 600；未提高
主机 CPU、内存或 I/O 限额。

```bash
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/search_ui_test.mjs test/e2e/ui_behavior_test.mjs
PYTHONPATH=python SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-safe.h4SzvX/vector-socket/gateway.sock \
  scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m2_vector_worker_test.py
python3 -B test/e2e/ui_contract_test.py
python3 -B test/e2e/m1_browser_self_test.py
# 仅在上面数据库/编码服务退出后，另一受限单元内运行：
SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome \
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/search_browser_test.py /controlled/evidence-directory
```

编码服务沿用向量部署手册的网络隔离：PrivateNetwork=yes、AF_UNIX 白名单、
共享模型锁；gateway.sock 在 0700 目录内，为 nobody 所有、0600。联合测试单元
smartdocs-gateway-verified 于 21:34:22 启动、21:36:51 正常结束，Result=success、
ExecMainStatus=0、MainPID=0。编码单元 21:37:01 正常停止，随后才启动 Chrome。
内存 high 计数 6157→6203，max/oom/oom_kill 保持 0；出现节流，不声称无内存压力。
测试收尾确认无运行中的 smartdocs 临时单元，生产 mysql 仍 active。

## 失败、审查与边界

1. 测试先行：新 HTTP 测试最初 10 项因模块未实现而失败；JS 新增 2 项分别因仍走
   `/search` 和未显示批准范围失败，旧 7 项通过。实现后转绿。
2. 沙箱拒绝创建回环 socket，改在获准的非 root 测试环境运行，并未放宽应用鉴权。
3. 初次联合单元 smartdocs-gateway-check 的 Python 74 项已通过，但系统旧 Node 不支持
   `--test`，退出码 9；尚未执行数据库/模型阶段。改用已缓存 Node v24.18.0 后完整重跑。
   不将首次失败单元算作成功。
4. 浏览器首轮功能通过；视觉复核发现人工夹具将 vector 命中项错误标为 pending，
   改成 completed 并重新运行浏览器、重新归档截图，生产逻辑未变。
5. 独立只读代码及部署文档审查无重要未修问题；补齐专用代理路由、服务文件名并
   对齐 M1 的 smart-docs.service 依赖名。

没有生产部署、真实域名/TLS/Nginx 联调、完整 M1 重验或 M2 综合验收；当前环境没有
nginx 命令，部署模板只经审查，须在目标部署时实际 `nginx -t` 和验证 unit。
标准库 HTTP 入口仅供受限同源反向代理使用，不宣称适合直接承受公网流量。
模型查询不保证语义召回完整，浏览器旧请求的在途编码也不保证被取消。
Agent/MCP 与 M3–M5 尚未交付，整个 P0 目标保持未完成。
