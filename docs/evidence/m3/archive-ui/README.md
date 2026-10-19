# 方案A：真实归档确认网页

2026-10-19；基线 `8bfcf66797d8110c4933eecc78735eae9512d4d9` 加本轮变更。
[源码SHA256](source-sha256.txt)列出的资源/测试逐文件与受限副本cmp一致。
本轮只扩展现有任务页，复用schema10与现有C++二进制；没有新增依赖、表或模型调用。
不是M3/M4或完整P0验收。

## 用户可见变化

- **View archive plan** 按需显示方案ID/修订/摘要、文件名、固定版本、源/目标目录和理由。
  全部为纯文本；普通目录API取得的源目录名仅用于本地界面，不进入模型上下文。
- 未勾选的确认框绑定当前可见修订，**Confirm this revision** 与 **Reject this revision**
  为独立决定；待定目标、已拒绝修订、阅读者或终止任务不能确认/执行。
- **Execute confirmed revision** 必须另点一次，只发送服务端绑定的方案/修订/操作ID。
  确认不移动文件，写入不自动重试，出错清除旧方案及动作。
- **Check archive receipt** 单独只读恢复，只渲染六字段历史回执；不暴露额外items/回答，
  不将历史提交说成文件当前所在位置。完整方案失效时仍可按后端规则读取回执。
- 项目、任务、本人角色变更、注销和乱序响应不会恢复旧方案或启用旧按钮。
  创建、归档写入及取消请求的锁均保留至实际请求结束；不以页面失效冒充请求结束。
  锁只属于当前页面，跨标签页重复操作由后端修订绑定和幂等事务处理。

## 验证与过程

1. 已有Agent行为基线15/15；[新增9项RED](red.txt)为缺少按钮/适配函数，原15项仍通过。
2. 初次接入后相关前端62/62。独立[审查](review.md)发现取消锁在invalidate时提前释放，
   [专门RED](cancel-red.txt)实证取消未返回时发出1次归档确认（期望0）。
   修复仅调整既有busy生命周期，不增加重复锁状态；同时覆盖取消成功及失败的迟到返回。
3. 补回执字段白名单/方案撤销后恢复，最终相关前端 **64/64**、页面契约 **8/8**，
   [完整输出](verified.txt)。不是全仓测试，也未重跑C++全量或22项归档HTTP回归。
4. [首次真实浏览器](browser-first.txt) **0/1**：页面拒绝、新修订和确认已经执行，
   六屏宽预览检查通过；随后测试错误地GET单文件更新路由，被真实服务返回405。
   改为既有文件列表接口按稳定ID核对位置，没有改业务API或放宽断言/限额。
5. [修正后的真实浏览器](browser-recheck.txt) **1/1**，两轮合计 **1/2**；本体22.148秒。
   真实Chrome/C++/MySQL覆盖拒绝后不能执行、新修订重新勾选、确认后源目录不变、独立执行后
   目录变更、只读回执、既有任务创建/取消/来源/审计及用户隔离。提案和回答是固定协议夹具，
   决定/执行由浏览器点击驱动，不把夹具当作模型生成或分类质量证据。
6. 360/375/390/414/768/1280px无所测面板横向溢出；预览按钮/确认标签至少44px高。
   已视觉检查[360px确认区](review-360.png)和[1280px确认区](review-1280.png)：未勾选、确认按钮
   禁用、理由中的script原样显示、长标识换行。另保留[360px回执](archive-360.png)与
   [1280px回执](archive-1280.png)；回执截图为视口内局部，不等于整页截图。
7. [资源清理](resources.txt)：本轮全部单元MainPID=0，slice TasksCurrent=0，业务MySQL仍为
   PID975。memory.events high保持80521，max/oom/oom_kill为0，无新增内存节流或OOM。
   所观察MemAvailable均高于512MiB；不是峰值内存、吞吐或长期稳定性承诺。

## 环境与复现

仍在当前ECS；全部重负载串行，受smartdocsvectorcheck.slice约束：CPU100%、
MemoryHigh1200MiB/Max1600MiB、Swap0、TasksMax256，/dev/vda双向5,000,000B/s和200IOPS。
User=nobody/Group=nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMaxSec900、
TimeoutStopSec5、KillMode=control-group；轻量Node验证单元RuntimeMaxSec120。
MemAvailable低于512MiB时停止本轮测试；不连接业务库。
wrapper创建独立skip-networking MySQL临时socket，完成后回收；无生产迁移/部署。

副本 `/tmp/smartdocs-safe.h4SzvX/repo`，PYTHONPATH为该副本的python目录。在受限单元中：

```bash
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs test/e2e/processing_lifecycle_test.mjs test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_agent_browser_test.py
```

浏览器另设置SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome、
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver、
SMARTDOCS_AGENT_BROWSER_OUTPUT为nobody所有的mktemp目录。
当前会话没有Node REPL浏览器工具，沿用仓库真实Chrome/WebDriver替代，沙箱开启；
不保存可能带登录凭据的driver日志。

## 未交付范围

没有方案编辑器、项目目标策略管理网页、MCP写工具发布、模型等待/继续编排、
个人交接清单/报告或五工具联调；本轮不调用DeepSeek、不进行生产迁移或部署。
下一步继续方案A的剩余业务基础和工具接入，完整目标保持活动。
