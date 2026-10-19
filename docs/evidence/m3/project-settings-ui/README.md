# 项目配置网页验证

2026-10-19；基线 `862caa99e87b6ec22dde79c6a0d50ed3e90b9c9f` 加本提交。
[业务说明](../../../api/m3-project-settings-ui.md)，[源码指纹](source-sha256.txt)。

## 已记录的验证

- 新增配置测试先 **0/7**：缺少模块及 PUT 适配器；见 [red.txt](red.txt)。
- 实现后相关前端 **82/82**、页面契约 **8/8**：[unit.txt](unit.txt)。
  覆盖默认不继承批准、修改撤销勾选、完整集合替换、空集合清空、UTF-8/稳定key校验、
  非管理员拒绝、迟到响应隔离、写锁跨项目失效保持、未知结果不自动重试及精确PUT载荷。
- 首轮真实浏览器 **0/1**：[browser-initial.txt](browser-initial.txt)。
  原因为 C++ 静态白名单漏注册新模块，app模块图不能初始化；不是内存不足。
  增加 socket 资源回归后先观察到 **0/1**：[route-red.txt](route-red.txt)，随后补齐精确路由。
- 修复后 server、smartdocs_tests、smartdocs-admin 串行构建成功，HTTP连接回归 **5/5**，
  包含新模块200、正确MIME/内容和未知资源404：[build.txt](build.txt)。
- 新构建的第二轮浏览器 **0/1**：[browser-startup.txt](browser-startup.txt)，Chrome会话创建失败，
  尚未进入页面断言。独立无登录空白页诊断随后通过：[startup-probe.txt](startup-probe.txt)，
  匹配的Chrome/Driver155.0.8059.84、沙箱开启，11.173秒创建会话。
  未确定启动失败根因，不将冷加载推测写成已修复；未改变浏览器参数、沙箱、超时或资源限额。
- 第三轮业务复验 **0/1**：[browser-layout-red.txt](browser-layout-red.txt)。已通过登录、reader拒绝、
  清单/目录保存、修改撤销批准和四种手机宽度；768px时设置面板发生横向溢出。
  原因为全局表单双列最小宽度10rem+16rem大于该断点下半宽面板；将设置表单限定为自适应单列。
- 布局修复后前端 **82/82**、契约 **8/8**；第四轮浏览器业务断言全部完成，但测试整体仍 **0/1**：
  [browser-cleanup-failure.txt](browser-cleanup-failure.txt)。测试最后本人降为reader，继承夹具仍用该账号
  取消待处理任务，按权限规则得到403。仅调整测试finally，改用已有的另一位管理员执行夹具清理，
  不恢复被测账号权限、不绕过服务端校验，也不将失败轮算成成功。
- 修正夹具后第五轮 **1/1**：[browser-before-checkbox.txt](browser-before-checkbox.txt)，完整业务及清理通过。
  随后的[截图复核](checkbox-before-360.png)发现长批准标签挤压checkbox；原测试只检查可点击label≥44px，未覆盖checkbox本体。
  将设置区checkbox固定为20px、不收缩，并加入六屏宽下本体宽高≥20px的断言。
- 最终第六轮：[verified.txt](verified.txt)，前端 **82/82**、页面契约 **8/8**、真实浏览器 **1/1**，
  浏览器测试本体58.634秒；整个单元退出0。六次浏览器尝试总计2次成功、4次失败，过程均保留，
  不将重复运行累计成更多场景，也不掩盖已发现的产品、环境和夹具问题。
  最终覆盖reader不可编辑且伪造PUT403；完整清单/目录批准、修改撤销勾选、旧任务启动409；
  清单并发修订409、目录改名导致旧策略409、重读后批准当前名称；空集合清空；本人降权清屏。
  最终0个归档方案、0条model_calls大于0的运行记录；没有启动Agent执行或移动文件。
  360/375/390/414/768/1280六屏宽设置面板无横向溢出，主要控件/确认label≥44px，checkbox本体≥20px。
  已视觉审阅最终[360px](settings-360.png)、[1280px](settings-1280.png)截图：批准状态清晰，长说明换行，
  脚本文字作为输入值而不执行。截图仅展示长设置页局部，完整业务由测试断言覆盖。
- 独立只读审查覆盖设置模块、API、HTML/CSS、项目/注销/角色集成，并复核静态白名单及回归；
  未报告阻塞问题。审查不代替实际浏览器测试。

## 复现与环境

当前 ECS，3495MiB RAM、无swap。所有编译/测试以nobody在共享 `smartdocsvectorcheck.slice` 串行执行：
CPU100%，MemoryHigh1200MiB/Max1600MiB，Swap0，磁盘读写各5MB/s和200IOPS。
NoNewPrivileges、KillMode=control-group；单元测试120秒，构建/数据库/浏览器900秒上限。
Chrome沙箱启用，复用已有Node、Python、Chrome/Driver，无下载依赖。

测试副本 `/tmp/smartdocs-safe.h4SzvX/repo`；修改源码与工作树逐文件cmp核对。
以下命令必须在上述限额内运行，不要在本ECS直接启动无限额验收：

```sh
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
bin/smartdocs_tests --filter=http_connection_
/tmp/smartdocs-safe.h4SzvX/bin/node --test \
  test/e2e/project_settings_ui_test.mjs test/e2e/agent_ui_test.mjs test/e2e/ui_behavior_test.mjs \
  test/e2e/processing_lifecycle_test.mjs test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_settings_browser_test.py
```

PYTHONPATH指向副本python；SMARTDOCS_TEST_PARSER_PYTHON指向既有parser虚拟环境；
SMARTDOCS_BROWSER_BIN/SMARTDOCS_WEBDRIVER_BIN指向既有Chrome/Driver；
SMARTDOCS_SETTINGS_BROWSER_OUTPUT为nobody可写的独立临时目录。
临时MySQL仅私有socket、32MiB缓冲池；wrapper验证schema15、重复迁移、拒绝半套DDL及103文件升级保留。
空root密码告警来自隔离测试库初始化，不涉及业务数据库。

本轮因新增静态资产白名单而重建后端；[构建指纹](binary-sha256.txt)均来自测试副本。
工作树bin为旧构建，未用于本轮验证；没有替换运行中的生产服务。

[资源终态](resources.txt)：本轮所有测试单元MainPID=0，共享slice TasksCurrent=0，原业务MySQL仍active。
memory.events始终high80521、max0、oom0、oom_kill0，没有新增内存节流或OOM。
所有采样MemAvailable均高于512MiB停止阈值，不声称测量过峰值RSS或长期冷启动成功率。
临时测试库、上传样本及浏览器profile由夹具清理，均为合成资料；日志和截图保留，不涉及用户业务数据。

## 范围限制

仅补充项目配置网页及静态资源路由，未改变业务数据库结构或后端权限规则。
未读取API key、调用付费模型、执行生产迁移或部署；不是全仓回归、模型效果或性能验收。
目录管理网页、受控执行入口、固定任务效果评测和完整演示仍待后续；M3/P0尚未验收。
