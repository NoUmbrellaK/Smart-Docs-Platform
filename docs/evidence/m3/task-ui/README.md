# M3 任务工作区增量

2026-10-18，基线 `ebda9766da8ae358b35c9300245962fcc1d95335`。
新增同源任务网页：明确问题/固定版本外发确认、pending创建、本人任务分页、
刷新/取消、回答草稿与精确来源。复用原生JS和既有API，无新依赖、迁移或模型调用。
契约及限制见[工作区说明](../../../api/m3-agent-workspace.md)。

## 测试与审查

- [初始RED](initial-red.txt)：新增模块未实现，6项新测试失败。
  更早一次[环境错误](environment-node12.txt)误用了系统Node12，不算业务RED；
  后续使用已有独立Node24，不安装系统依赖。
- [串行增量构建](build.txt)：server、smartdocs_tests、smartdocs-admin成功。
  C++仅增加`/js/agent.js`静态白名单，不改业务存储。
- [审查](review.md)发现页面失效可能释放非幂等创建锁；
  [有效RED](race-red.txt)复现两次POST。早先[测试编排挂起](race-harness-stopped.txt)
  因误等待未决promise被主动停止，保留记录，不计作有效RED。
- [最终GREEN](reviewed-green.txt)：前端行为 **45/45**（新增Agent7项）、
  页面契约 **8/8**、C++静态白名单边界 **1/1**，失败/跳过均0。
  包含实际app启动接线的注销迟到响应测试；多数行为为替身DOM/API，不冒充浏览器。
- [首次真实浏览器](browser-startup-failure.txt)：Chrome会话创建失败，测试 **0/1**，
  未进入页面断言。没有OOM/内核异常记录；独立空白页启动诊断随后通过。
  根因未被确定，不能将冷加载推测写成已修复；[独立启动日志](startup-probe.txt)。
- [同配置业务复验](browser-recheck.txt) **1/1**通过，测试本体35.598秒，整个单元退出0。
  真实创建/取消/重载、脚本回答与固定出处、纯文本防HTML执行、外发批准撤销后清空、
  切换用户不能看他人任务均通过；360/375/390/414/768/1280px布局无面板横向溢出，
  所检按钮/textarea高度至少44px。已视觉检查[360截图](agent-360.png)和
  [1280截图](agent-1280.png)：内容换行、状态与纯文本夹具正确。截图为视口内局部，
  并不完整展示整张长表单；全场景由测试断言支撑。

[源码哈希](source-sha256.txt)与执行副本逐文件cmp一致；[二进制哈希](binary-sha256.txt)
绑定实际执行产物。[最终状态](final-status.txt)记录本轮单元均无运行进程，复验和构建
正常退出；首次浏览器失败及RED状态如实保留。memory.events从high80272/max0/oom0/
oom_kill0到相同值，无新增节流或OOM。原MySQL仍PID975，隔离测试数据库和上传资料
由夹具清理，不涉及业务数据；只保留两张不含凭据的合成资料截图，不归档浏览器profile。

## 复现与资源

执行副本`/tmp/smartdocs-safe.h4SzvX/repo`。以下命令均放入受限systemd单元，
不可在这台服务器无约束并行运行：

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
/tmp/smartdocs-safe.h4SzvX/bin/node --test --test-concurrency=1 \
  test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs \
  test/e2e/search_ui_test.mjs test/e2e/processing_ui_test.mjs \
  test/e2e/processing_lifecycle_test.mjs
/usr/bin/python3 -B test/e2e/ui_contract_test.py
bin/smartdocs_tests --filter=http_connection_streams_only_the_allowlisted_static_shell
PYTHONPATH=python bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_agent_browser_test.py
```

浏览器环境：SMARTDOCS_BROWSER_BIN为`/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome`，
SMARTDOCS_WEBDRIVER_BIN为同根下`chromedriver-linux64/chromedriver`；
SMARTDOCS_AGENT_BROWSER_OUTPUT为单独的nobody可写临时截图目录。
使用现有原生沙箱与about:blank启动页，不保存登录期driver日志。
测试自建禁网络临时MySQL；初始化空密码警告不属于生产库。

单元归属`smartdocsvectorcheck.slice`：MemoryHigh=1200MiB、MemoryMax=1600MiB、
MemorySwapMax=0、CPUQuota=100%、TasksMax=256、/dev/vda读写各5MB/s和200IOPS。
User=nobody、Group=nogroup、Nice=10、UMask=0077、NoNewPrivileges=yes、
TimeoutStopSec=5、KillMode=control-group；重型单元串行，RuntimeMaxSec=900，
轻量测试/空白启动诊断120或180秒。主机可用内存低于512MiB停止。限额未放宽。

## 证据边界

真实浏览器用例使用真实Chrome/C++/隔离MySQL、本地解析和鉴权来源读取；
回答由测试通过真实run/start/check/finish协议保存，清楚标注Scripted UI fixture。
它验证页面如何呈现持久结果和引用，不证明模型推理、自动执行或DeepSeek质量。
未读取密钥、未请求远程API，没有生产部署或业务库迁移。没有重跑全仓C++/Python
或完整M1验收，不将历史结果累计为本轮全量通过。

本页不自动领取/执行pending任务；仍需可信CLI。服务端创建非幂等，页面在途锁
和明确新草稿流程不能提供跨标签页/完全重载的全局去重。外部权限变更在下次读取
时检测，不保证撤回已显示信息。完整工具调用审计、生产执行入口、三个写工具、
真实供应商验证以及M3整体/P0验收均未完成。
