# A16 网页显式执行与未知结果恢复

2026-10-19；基线 `17bc18ca79853d00da6a8c632859c676df24a4d7`，分支
`codex/m3-mcp-agent`。复用已有执行入口、原生JS及schema15，不新增依赖、队列或迁移。
接口见[工作区](../../../api/m3-agent-workspace.md)；被测文件见[SHA256](sha256.txt)。

## 最终实际结果

最终代码的前端 **90/90**、页面契约 **8/8**（[日志](mobile-browser-start-failure.txt)中的前两段），
独立HTTP→MCP→C++→SQL回归 **3/3**（[日志](http-green.txt)），
真实浏览器最终复验 **1/1**（[日志](browser-final-green.txt)），均无跳过。
浏览器四次实际尝试合计2成功/2启动失败，不包含203/EXEC未启动的命令；
首次业务通过后的截图问题已修正，最终六屏宽同时检查复选框可见宽度。
[手机截图](execute-360.png)、[桌面截图](execute-1280.png)已人工复核。

本轮全部测试单元MainPID=0、共享组TasksCurrent=0，原MySQL仍active；
[终态](resources.txt)high80521/max0/oom0/oom_kill0与基线相同，无新增节流/OOM。
采样可用内存均高于512MiB停止阈值，不声称测量了峰值或证明长期稳定性。
八个变更源码/测试文件与受限副本cmp一致，二进制未重建；无真实付费请求。

## 范围与检查

- 创建仍只产生pending；查看后重新勾选可能计费的启动确认，不自动执行。
- 仅发送同源固定任务路由及空JSON，拒绝重定向，300秒浏览器截止期，无POST重试。
- 在途执行锁跨刷新/项目切换/注销保留，刷新/取消仍可用；旧按钮和迟到响应不能回填。
- 网关返回不是业务成功依据，重新读取C++状态及受保护回答/报告/方案。
- 网络错误或丢失响应只提示未知并要求刷新，页面重载不触发模型，不自动确认/执行归档。

浏览器使用真实Chrome→测试专用同源代理→Agent HTTP→stdio MCP→C++→临时MySQL，
只用HTTPX替换供应商网络。一个场景包含三个真实任务：固定引用回答及来源/轨迹、
提交后人为丢失执行响应并只读恢复、运行期间刷新并取消且重载不重发。
每次完整场景5个合成供应商响应；合成请求计数不是付费token或真实质量证明。
六屏宽为360/375/390/414/768/1280，检查无横向溢出、44px按钮/标签和可见确认框。

## 失败过程与复核

1. [初始RED](missing-execution-red.txt)：Agent模块44项中6项失败、38项通过；
   新增的非pending拒绝测试原已通过，其余执行功能缺失。实现后
   [首次前端回归](initial-unit-green.txt)89/89、页面契约8/8通过。
2. [只读审查](review.md)后补截止期/计时器清理测试，前端90/90；
   [首次浏览器运行日志](browser-first-failure.txt)含此结果，但Chrome会话创建失败，
   未进入业务页面。增加私有驱动日志后同配置复验能启动；首次启动根因尚未确定。
3. [复验命令路径失败](runner-path-failure.txt)：systemd把相对脚本路径解析到/root工作树，
   nobody无法访问，203/EXEC；改用已有/tmp副本绝对路径，不放宽目录权限。
4. [浏览器通过及后续夹具失败](browser-green-http-setup-failure.txt)：浏览器场景1/1通过。
   关闭/刷新页面时测试代理出现BrokenPipe日志，不是业务失败；随后HTTP套件在同一库
   重复创建固定worker-e2e-admin账号，setUpClass失败、0项运行。改用独立临时库复验，
   不修改产品账号或权限逻辑。原始驱动日志包含一次性测试会话，仅保留私有临时目录，
   不提交；仓库仅保存合成资料截图和不含凭据的测试摘要。
5. 首轮通过截图的人工复核发现长标签会挤压手机确认框；原尺寸检查只检查标签和按钮，
   覆盖不足。[修复前截图](mobile-checkbox-before.png)及[契约RED](mobile-checkbox-red.txt)保留；
   新增不收缩样式契约及真实浏览器20px复选框宽度断言，修复限定Agent详情。
   [独立HTTP复验](http-green.txt)3/3通过，无跳过。
6. [样式修复后的启动失败](mobile-browser-start-failure.txt)：前端90/90、契约8/8通过，
   Chrome再次在会话创建阶段失败，尚未运行新增宽度断言。[启动诊断](mobile-browser-start-diagnosis.txt)
   显示驱动约64秒后无法连接Chrome；没有新增内存节流/OOM。限额下启动耗时是待查因素，
   不能据此认定根因。保持相同限额/沙箱/代码做重复性复验，不加自动重试掩盖失败。

## 复现与资源

沿用ECS受限组`smartdocsvectorcheck.slice`：CPU单核、MemoryHigh1200MiB、Max1600MiB、
Swap0、TasksMax256，磁盘读写各5MB/s及200IOPS。User=nobody、UMask0077、Nice10，
同一时间仅一个串行测试单元，RuntimeMaxSec900、KillMode=control-group。
副本`/tmp/smartdocs-safe.h4SzvX/repo`；不重建C++，使用已验证的既有二进制。

环境：PYTHONPATH指向副本python，SMARTDOCS_TEST_PARSER_PYTHON指向已有parser环境，
SMARTDOCS_BROWSER_BIN及SMARTDOCS_WEBDRIVER_BIN使用已有Chrome/driver，
SMARTDOCS_EXECUTE_BROWSER_OUTPUT指向独占临时输出目录。在上述限额中串行执行：

```sh
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/*_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_execute_browser_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_agent_http_test.py
```

两个数据库包装器须分开：私有socket、无TCP、32MiB缓冲池，结束清理临时库。
初始化空root密码警告只涉及此隔离库；未改原业务MySQL、上传或生产schema。
浏览器沙箱保持开启，不借用root运行测试。资源计数基线high80521/max0/oom0/oom_kill0。

## 限制与后续

无真实key读取、付费模型请求、新依赖或生产部署。本轮不是全仓C++/Python全量回归，
也不验证生产TLS/反向代理安装、真实模型质量或持续负载。执行服务须另行部署；
目录管理、完整审计/固定任务评测及演示仍待后续，M3/P0尚未验收。
