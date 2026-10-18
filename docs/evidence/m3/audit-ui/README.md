# M3 调用轨迹网页增量

2026-10-19，基线`4de31c77b47a7660bed5a32e6069be50b15381c8`。
需求第4节工具调用摘要接入现有[任务工作区](../../../api/m3-agent-workspace.md)：
点击加载/刷新才读取后端轨迹，以原生details展开名称、请求ID、UTC时间、脱敏参数、
后端耗时和状态。空记录不证明完成或没有模型调用，unknown不是成功，null耗时不是0。
不显示任意参数对象、原查询或正文；不增加依赖、后端代码、迁移或模型执行入口。

## 验证

- [基线](baseline.txt)：原Agent行为7/7。
- [初始RED](initial-red.txt)：原7项通过、新6项因缺少API或摘要动作失败。
- [前端GREEN](frontend-green.txt)：相关前端行为 **51/51**，页面契约 **8/8**，
  失败及跳过均0。实际运行AgentPanel与同源API模块，DOM/HTTP依赖用测试替身，
  不把这组当作真实浏览器证据。
- [独立只读审查](review.md)：无阻塞项；浏览器运行由主代理负责。
- [真实浏览器GREEN](browser-green.txt)：Chrome/C++/隔离MySQL **1/1**，0失败/跳过，
  单元正常退出0，测试本体126.842秒。真实搜索、chunk读取、参数拒绝产生三条后端
  记录并展示；空记录提示、展开、脱敏、批准撤销后回答/来源/摘要全部清空、他人登录
  不见本人任务通过。回答仍为脚本化协议夹具，不冒充真实模型质量。
  360/375/390/414/768/1280px无工作区横向溢出，按钮/textarea/summary均至少44px。
  已视觉检查[360局部截图](audit-360.png)和[1280局部截图](audit-1280.png)：
  长请求ID和时间换行、展开详情与成功/拒绝状态正常；截图只覆盖轨迹区域。

[源码哈希](source-sha256.txt)对应本轮全部静态资源及测试修改，和实际执行副本逐文件
cmp一致。[二进制哈希](binary-sha256.txt)绑定未重建的实际测试产物。
[最终状态](final-status.txt)记录本轮单元无运行进程；成功单元已被systemd回收，
成功终态见各journal。memory.events基线和终值均为high80521/max0/oom0/oom_kill0，
本轮无新增节流或OOM。原MySQL仍PID975，隔离数据库/上传资料由夹具清理，未删除
业务资料。仅归档两张脱敏截图，不保存浏览器profile或登录期driver日志。

## 复现和资源限制

使用已有隔离工作区及`/tmp/smartdocs-safe.h4SzvX/repo`执行副本。静态文件无需编译，
沿用上一增量schema8二进制；未重跑全仓C++、Python或实际DeepSeek。

```bash
/tmp/smartdocs-safe.h4SzvX/bin/node --test --test-concurrency=1 \
  test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs \
  test/e2e/search_ui_test.mjs test/e2e/processing_ui_test.mjs \
  test/e2e/processing_lifecycle_test.mjs
/usr/bin/python3 -B test/e2e/ui_contract_test.py
PYTHONPATH=python \
SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome \
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver \
SMARTDOCS_AGENT_BROWSER_OUTPUT=/tmp/smartdocs-audit-ui.5KCeIk \
  bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_agent_browser_test.py
```

以上命令仅在串行受限systemd单元运行：smartdocsvectorcheck.slice，MemoryHigh1200MiB、
MemoryMax1600MiB、Swap0、CPU100%、Tasks256、/dev/vda读写各5MB/s和200IOPS；
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、TimeoutStopSec5、
KillMode=control-group；前端单元最多180秒，浏览器900秒。主机可用内存低于512MiB
停止测试，不放宽配额、不清缓存、不并行启动数据库/浏览器。浏览器使用既有原生沙箱。

## 未完成范围

这只是既有后端请求轨迹的网页展示，不是完整MCP或模型审计。行数可能包含hybrid的
预检和实际请求，不能充当MCP调用数；耗时不含模型生成，不提供token或费用。
跨客户端权限变化在下次服务端访问时发现，不承诺撤回已展示信息。没有后台轮询，
当前页面仍只创建pending任务。生产执行入口、完整五工具、归档与交接报告及M3/P0
验收仍待完成；未迁移生产库、未部署服务、未读取真实API密钥或调用DeepSeek。
