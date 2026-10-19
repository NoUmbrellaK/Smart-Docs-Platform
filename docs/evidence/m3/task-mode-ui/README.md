# 网页任务模式验证

2026-10-19；基线 `3db262381fae224c2cf4cd0693ee4c6563a303ed` 加本提交，
源码指纹见 [source-sha256.txt](source-sha256.txt)。[业务说明](../../../api/m3-task-mode-ui.md)。

## 实际结果

- 新模式测试先失败：最初模拟 DOM 对未绑定事件抛异常，修正为与浏览器一致的无动作行为；
  第二轮 **37 项，32 通过/5 失败**，明确捕获缺少模式保护、错误创建和缺少适配接口。
  分别保留 [red-harness.txt](red-harness.txt)、[red.txt](red.txt)，没有隐去初始夹具问题。
- 实现后相关前端 **75/75**、页面契约 **8/8**：[unit.txt](unit.txt)。
  覆盖普通问答不加载额外配置、只提交互斥修订、未批准/空/非法/拒绝读取阻断、
  reader 禁归档、文本安全、迟到响应、并发读取、创建锁和未知结果不自动重试。
- 真实 Chrome→C++→隔离 MySQL **1/1**，单次运行，无重跑：[browser.txt](browser.txt)。
  reader 从缺失清单阻断到加载后创建交接；普通问答无附加修订；清单撤销后提交409且任务总数不增；
  reader 伪造归档请求403；editor 创建归档pending；在页面加载后降为reader再次创建403。
  最终共3个pending任务、0个模型运行记录、0个归档方案，文件位置未变。
- 360/375/390/414/768/1280 六屏宽，无表单横向溢出，按钮/选择框/文本域/确认标签≥44px。
  已人工检查 [360px](mode-360.png)、[1280px](mode-1280.png) 实际截图；长文字折行，脚本文字不执行。
- 独立只读审查覆盖前端四文件、修订与权限边界、注销/项目失效集成；未发现具体阻塞问题。

## 复现与环境

当前 ECS，3495 MiB RAM、无swap。测试均以nobody在共享 `smartdocsvectorcheck.slice` 串行运行：
CPU 100%，MemoryHigh 1200MiB/Max1600MiB，Swap0，磁盘读写各5MB/s及200IOPS。
RuntimeMaxSec：单元测试120秒，数据库/浏览器900秒；NoNewPrivileges，KillMode=control-group。
浏览器沙箱保持启用。Node、Python、Chrome/Driver复用已有受控安装，没有下载依赖。

测试副本 `/tmp/smartdocs-safe.h4SzvX/repo`；测试前后用cmp核对修改文件与工作树一致。
下列命令是在上述限额内执行，不应绕过资源保护直接在生产环境运行：

```sh
/tmp/smartdocs-safe.h4SzvX/bin/node --test \
  test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs \
  test/e2e/processing_lifecycle_test.mjs test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_task_mode_browser_test.py
```

环境：PYTHONPATH指向副本python；SMARTDOCS_TEST_PARSER_PYTHON指向既有parser虚拟环境；
SMARTDOCS_BROWSER_BIN/SMARTDOCS_WEBDRIVER_BIN指向既有Chrome/Driver；
SMARTDOCS_TASK_MODE_BROWSER_OUTPUT为nobody可写的独立临时目录。
临时MySQL仅私有socket、32MiB缓冲池，不监听网络。wrapper验证schema15、重复迁移、拒绝半套DDL和
103文件升级保留；日志中的空root密码告警仅来自该隔离测试库初始化，不涉及业务数据库。

不重编译后端，沿用前序schema15构建：server SHA256
`79fe2f20ae89579d1956980e31c4810f10315185054cd86571893f25053dd74a`，admin SHA256
`b6d30f014742ab86b994e7a00229d84662f2ca60c47fe01569fcac3b9bea0fec`。
工作树bin是更旧的本地构建，未用于本轮测试；自`75db331`以来code没有变化。

[资源终态](resources.txt)：所有测试MainPID=0、共享TasksCurrent=0，业务MySQL仍active。
memory.events前后均high80521、max0、oom0、oom_kill0；无新增内存节流/OOM。
测试临时数据库/浏览器profile按既有夹具清理，仅为合成数据，不保留恢复副本；证据截图和日志保留。

## 不代表的结果

没有读取API key、付费模型请求、生产迁移或部署；不是全仓C++/Python回归、模型质量评测或性能验收。
此处只证明网页模式创建和既有后端门禁，未启动Agent执行循环，不代表三种模式完整端到端演示。
项目配置维护网页、受控调度、固定任务评测仍需后续增量；M3/P0尚未验收。
