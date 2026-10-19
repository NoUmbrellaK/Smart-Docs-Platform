# 方案A：真实确认、原子归档与持久回执

2026-10-19；基线378bd91bc63ebea86e3261ae6dbda2b4ec28a1cf加本轮变更。
[源码SHA256](source-sha256.txt)逐文件与受限副本cmp一致。
本轮为一个C++业务基础增量，不代表M3/M4或P0整体交付。

## 范围和已验证结果

- 方案绑定用户/修订/内容摘要，confirm/reject独立同源请求；确认不移动文件。
- 新修订作废旧确认，服务端生成唯一操作ID；两种等待状态释放运行槽、可取消、重启保留。
- 全范围、源版本/位置/名称、目标批准、冲突及取消重验；文件位置、完整结果、审计及completed同事务。
- 真实临时库HTTP/SQL **22/22**通过（9项既有策略/预览、13项新增确认/执行边界）。
- JS行为回归 **53/53**、页面契约 **8/8**通过。
- C++相关回归 **31/31**通过、0失败、0跳过：M3 17、schema 6、auth_project 3、file_mutation 3、authorization 2。
  不是全仓C++全量复验。真实Chrome复验 **1/1**，两轮业务场景合计 **1/2**（首次启动失败）。
  360/375/390/414/768/1280px无面板横向溢出，所检按钮/textarea至少44px；无自动确认/模型调用。
- 新库、重放、部分schema拒绝、103份旧文件升级检查通过；不回填授权、操作或结果。
- 独立[静态审查](review.md)发现一个数据库绑定弱项，已复现、修复并复核，无剩余审查项。

## 关键可复现边界

| 场景 | 真实断言 |
| --- | --- |
| 未确认/伪造批准 | 未确认execute409，额外approved字段400；确认前后源位置不变 |
| 决定/修订 | 拒绝后不可确认同修订；旧摘要、旧修订、待定目标不能执行；显式新修订作废旧操作ID |
| 授权重验 | 他人404、跨Origin403、编辑降为阅读者403、范围内未提案文件撤销/新版也阻止执行 |
| 快照与冲突 | 源位置变化、目标改名撤销、确认后新增同名占位均阻止整批移动 |
| 整批回滚 | 临时SQL触发器仅在第一文件已移动、第二文件即将移动时SIGNAL；503后两文件均未移动，结果/审计为0 |
| 恢复与幂等 | HTTP响应读头后丢弃正文，重启再查回执；文件随后人工移回，再重放不重复移动/审计 |
| 竞争与取消 | 两个并发相同execute都返回相同结果且只有一次审计；取消先提交阻止执行，提交后取消不撤销归档 |
| 等待与预算 | 原running转等待后另两个任务可占槽、第三个429；重启保留确认及累计model_calls；过期running不能发布方案 |
| 数据库约束 | 直接INSERT别的任务/方案错配记录，被复合外键拒绝，不依赖HTTP校验 |
| 失效来源回执 | 外发批准撤销后完整重放拒绝，仍可读取仅六字段的内容无关持久回执 |

执行原子性测试是故障注入，不是整机断电/磁盘损坏恢复演练；取消测试覆盖两种提交先后，
不声称已做数据库锁内暂停的并发取消测试。最小回执只证明历史提交，不证明文件现位置。

## 过程记录

1. [RED](red.txt)：新10项HTTP均失败（缺少接口/等待状态/预算限制），新增2项DOM测试失败。
2. [单线程构建](build.txt)：server、smartdocs_tests、smartdocs-admin成功。
3. [外键RED及前端GREEN](schema-red-and-ui.txt)：前端53+8通过；22项HTTP/SQL中21通过，
   唯一失败是审查后新增的跨任务决策插入约束，确实成功插入不合法记录。
4. 改为(task_id,plan_id)复合外键，不修改HTTP事务算法；用新临时库重新跑完整22项。
5. [启动路径失败](launch-path-failure.txt)：systemd-run解析相对可执行路径到root工作树，nobody无法访问，
   203/EXEC，未启动测试。改成隔离副本绝对路径，没有修改目录权限或放宽资源上限。
6. [最终后端验证](verified.txt)：修复后的新临时库22项HTTP/SQL及31项C++全部通过。
7. [首次浏览器](browser-startup-failure.txt) **0/1**：Chrome会话创建失败，未进入页面断言。
   [独立无登录空白页诊断](browser-startup-probe.txt)随后通过；没有修改生产代码、浏览器参数、
   沙箱、超时或资源上限。根因未确定，不能将冷加载推测写成已修复。
8. [同配置业务复验](browser-recheck.txt) **1/1**通过，测试本体36.275秒。真实C++/MySQL/Chrome
   覆盖既有任务创建/取消/来源/审计/撤销清理、两种归档等待状态和提交回执、不同用户隔离。
   已视觉检查[360px](archive-360.png)和[1280px](archive-1280.png)：长ID换行、状态区分正确。
   截图仅为视口内局部，不完整显示长表单；列表明示snapshot-only，详情刷新不冒充整页列表同时刷新。
9. [最终资源与清理](resources.txt)：所有本轮单元MainPID=0，slice无剩余进程；测试库由wrapper清理。
   业务MySQL PID975仍active。memory.events high全程80521、max/oom/oom_kill均0，无新增节流/OOM。
   所观察MemAvailable均高于512MiB停止阈值；未测量峰值RSS、吞吐或长期冷启动成功率。

## 运行约束及复现

全部重任务串行置于smartdocsvectorcheck.slice，CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、TasksMax256；/dev/vda双向5,000,000B/s与200IOPS。
User=nobody/Group=nogroup、Nice10、UMask0077、NoNewPrivileges、RuntimeMaxSec900、
TimeoutStopSec5、KillMode=control-group。MemAvailable低于512MiB必须停止本轮测试。
不连接业务库，只用wrapper创建的独立skip-networking MySQL8.0.46临时socket；退出自动清理。
副本/tmp/smartdocs-safe.h4SzvX/repo，PYTHONPATH指向其python目录；无新依赖或模型调用。

在上述受限单元、被测副本工作目录中执行：

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs test/e2e/processing_lifecycle_test.mjs test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /bin/bash -c '
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_archive_execution_test.py &&
  stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
  stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
  stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
  stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
  stdbuf -oL bin/smartdocs_tests --filter=authorization'
```

浏览器单独使用相同限额、新临时库，在PYTHONPATH之外设置
SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome、
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver，
SMARTDOCS_AGENT_BROWSER_OUTPUT为nobody所有的mktemp目录；执行：

```bash
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_agent_browser_test.py
```

当前会话没有Node REPL浏览器工具，使用仓库既有真实Chrome/WebDriver测试替代，原生沙箱开启。
无登录启动诊断复用既有agent_browser_startup_probe.py，不访问业务页面、不传凭据；
业务测试不保存可能含凭据的driver日志。诊断通过不能代替业务页面验证或长期稳定性证据。

## 未交付范围

未发布MCP写工具、没有真实用户归档确认网页、没有模型等待/继续编排、没有个人交接报告。
浏览器夹具通过真实业务API生成确认/执行，只验证状态显示及回执，不冒充确认页或模型分类验收。
没有远程模型调用、生产迁移/部署；下一增量继续确认页面和后续工具编排，完整P0目标保持活动。
