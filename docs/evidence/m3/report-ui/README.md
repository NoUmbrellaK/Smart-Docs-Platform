# 个人交接报告网页验证

2026-10-19；基线 `f95bd6625b4100de41fa462d8dfbf5a076708c76` 加本轮变更。
本轮只修改页面资源、测试与文档，复用现有C++/schema15，不新增依赖或重新构建后端。
没有读取DeepSeek key、调用付费模型、生产迁移或部署。

## 已实现边界

报告任务与普通回答/归档回执分开展示；页面读权威报告，显示个人草稿、清单修订、
选择范围、实际准入来源、逐项状态、引用与覆盖限制，不把结构校验说成业务验收。
正文全按纯文本渲染，不采用模型提供的URL。来源点击重验全部报告依赖再读固定片段。
Markdown从固定受保护路径重新鉴权获取，错误响应不能下载为文件；迟到响应丢弃。
权限失败清空旧报告和来源；项目、任务、角色和注销使旧按钮失效。
不增加缓存、分享、自动重试或模型调用。下载到用户设备的副本无法远程撤回。

## 测试过程

新增行为测试先失败，见 [red.txt](red.txt)：31项中4失败、27通过，失败为缺少报告标识、
来源/下载按钮及接口；既有行为保持通过。最初的迟到响应测试未证明请求实际发出，
后续补强该断言，并补报告身份不匹配/移除引用拒绝测试，避免空操作也算通过。
初版前端回归69/69；最终结果另列，不将重复运行累计成更大成功数量。

首轮[真实浏览器日志](browser-first.txt)：页面契约8/8及隔离迁移通过，浏览器0/1，
在创建会话时报 session not created，尚未进入页面断言。运行时曾观察Chrome处于
wait_on_page_bit_common，但不足以确定根因；没有OOM或内存节流，不宣称是产品缺陷或已修复。
确认单元MainPID=0后，仅安排一次相同沙箱/限额/代码的新会话复验，未调整Chrome参数。

真实浏览器测试使用公开合成报告，通过真实协议保存，而非SQL伪造报告；
保存前按真实工具回执准入含未引用资料的依赖集合。报告是协议夹具，不是模型生成质量证据。
真实reader登录后阅读报告、点击固定来源、下载文件逐字节对照服务端附件；
撤销未引用来源后分别验证下载与来源失败清屏；另一个管理员不能访问该个人报告。
布局检查360/375/390/414/768/1280px，报告区无横向溢出，按钮至少44px高。

独立只读审查未发现确认的阻塞问题；特别要求用真实文件保存而不是mock或下载开始事件
证明Blob生命周期正确。审查者未改文件或运行测试，运行证据由主执行者提供。

## 环境和复现

当前ECS串行执行，沿用smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB、
MemoryMax1600MiB、Swap0、TasksMax256、/dev/vda双向5MB/s和200IOPS。
User=nobody、Group=nogroup、Nice10、NoNewPrivileges；轻量单元120秒，浏览器单元900秒，
结束回收进程组；MemAvailable低于512MiB则停止本轮测试。
临时MySQL仅socket、32MiB缓冲且无PerformanceSchema，不连接原业务库。
当前没有Node REPL浏览器工具，使用现有Chrome/WebDriver，沙箱开启，不记录driver凭据日志。

在副本 `/tmp/smartdocs-safe.h4SzvX/repo`，PYTHONPATH指向其python目录，串行运行：

```bash
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs test/e2e/processing_lifecycle_test.mjs test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_report_browser_test.py
```

浏览器环境：SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome，
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver，
SMARTDOCS_REPORT_BROWSER_OUTPUT指向nobody所有的独立临时截图目录。

## 最终结果

[最终日志](verified.txt)：增强前端回归 **70/70**，真实Chrome/C++/临时SQL复验 **1/1**，
浏览器两轮合计 **1/2**。复验测试本体63.356秒，不是性能SLA；没有改参数或放宽资源上限。
页面契约 **8/8** 见首轮日志，本轮没有修改HTML；初始失败保留，不归并为全成功。
真实下载文件与服务端Markdown附件逐字节一致，证明本次Chrome中Blob下载和同步URL释放可用；
不是跨浏览器兼容矩阵。角色为reader，整条浏览器读取/导出链路没有写业务内容或调用模型。

已视觉检查[360px](report-360.png)与[1280px](report-1280.png)：纯文本注入样例、
草稿提示、长ID换行及范围分组正常；截图是滚动后的视口局部，不代替整个报告的自动布局断言。
[指纹](source-sha256.txt)对应当前资源、测试及复用的后端二进制；被执行副本逐文件cmp一致。
[资源记录](resources.txt)确认本轮四个单元MainPID=0、slice TasksCurrent=0，原MySQL仍运行；
memory.events high保持80521、max/oom/oom_kill为0，即本轮无新增节流/OOM。

## 未覆盖与下一步

没有重跑全仓C++/Python、真实供应商效果、全五工具业务集或最大规模性能测试。
网页创建任务仍是普通问答，清单/归档模式选择、项目配置网页和模型调度尚待接入；
本轮不宣称M3/M4或完整P0验收通过。
