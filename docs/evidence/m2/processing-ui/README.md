# M2 固定版本处理状态与人工重试工作台

日期：2026-10-17；基线 `a35a9c0c106b0288fcbc02a7cc735e6f892b1030`。
复用既有任务查询、取消和重试接口，仅为 document 响应补充持久 `parse_job_id`，
不新增数据库迁移、依赖、轮询或自动重试。具体行为见[接口说明](../../../api/m2-processing-api.md)。
源码指纹见 [source-sha256.txt](source-sha256.txt)。

## 实测范围

- 完整 C++ **141/141**，0失败、0跳过；包括精确静态资源/MIME/未知路径404、固定
  版本任务ID、角色/历史来源、处理/向量/搜索以及原 M1 上传恢复和下载回归。
- 前端行为 **37/37**：新增任务面板9项、真实 app 登录退出生命周期1项、原搜索9项及
  原工作台18项。覆盖持久任务发现、独立状态、精确任务操作、单飞提交、角色/历史
  只读、晚到请求隔离、回执不明确不自动重放、文本安全及同源空正文 POST。
- 页面契约 **8/8**。快速 JS 测试使用受控 DOM/fetch 替身，不冒充浏览器验收。
- **真实浏览器组合场景1/1**，18.962秒（不含建库）：真实 Chrome 沙箱→C++→临时
  MySQL，无模拟任务 API；人工 TXT 真实解析并写关键词索引。覆盖 failed→retry→
  pending→cancel→reload→retry→completed；批准后向量任务独立取消/重试；reader
  无写按钮且 HTTP 写操作403；软删除后状态刷新404并清除任务；批准变更清除搜索。
  360/375/390/414/768/1280 六种视口无面板横向溢出，按钮高度至少44px。
- schema 5 初始化/重放及103文件升级夹具通过。源码与非 root 被测副本逐文件 cmp
  一致；移动端和桌面截图经过视觉检查。

最终 [联合测试输出](verified-check.txt)、[服务及资源状态](unit-status.txt)已归档。
界面截图：[360px](processing-360.png)、[1280px](processing-1280.png)。
最终单元 `smartdocs-processing-ui-accepted.service` 于北京时间22:09:00启动、22:25:06
成功退出，CPU累计44.015秒；时长包含受限建库、浏览器及完整数据库测试，并非应用
响应时间或性能结果。退出后没有运行中的 smartdocs 临时测试服务，原 MySQL active。
共享 slice 最终累计 memory.events high=10601，max/oom/oom_kill=0；存在内存节流，
不声称无内存压力，也不依据本轮结果取消 CPU/内存/I/O 限额。

## 复现方法

在当前 ECS 非 root 副本 `/tmp/smartdocs-safe.h4SzvX/repo` 串行运行：

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/processing_ui_test.mjs \
  test/e2e/processing_lifecycle_test.mjs test/e2e/search_ui_test.mjs test/e2e/ui_behavior_test.mjs
python3 -B test/e2e/ui_contract_test.py
export PYTHONPATH=python
export SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome
export SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver
export SMARTDOCS_PROCESSING_BROWSER_OUTPUT=/controlled/evidence-directory
scripts/test-with-mysql.sh -- /bin/bash -c \
  '/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/processing_browser_test.py && stdbuf -oL bin/smartdocs_tests'
```

所有重型命令必须置于受限 systemd 单元中，不可直接并行执行上述命令。
共享 `smartdocsvectorcheck.slice`：MemoryHigh=1200 MiB、MemoryMax=1600 MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256；/dev/vda 读写各 5 MB/s、200 IOPS。
测试服务 User=nobody、Nice=10、RuntimeMaxSec=1800。浏览器启用沙箱，数据库只用
隔离临时 socket，测试退出自动清理；原业务库未升级。未读取 DeepSeek env、未调用
付费 API、未运行向量编码器。浏览器内置工具在本会话不可用，沿用仓库已有 WebDriver。

## 失败记录与审查

1. 测试先行：新增 UI 测试最初因模块/接口未实现失败；新增 C++ document 断言因
   缺少 `parse_job_id` 失败，随后实现持久任务发现。
2. 第一轮联合测试在准备失败任务时提交了不存在的 `parse_timeout`，后端正确拒绝；
   改为契约允许的 `worker_failed`。这轮没有执行浏览器操作或完整 C++ 回归。
3. 只读审查发现上游文件版本请求可能在退出登录期间重新填充面板。新增实际 app
   bootstrap/事件处理测试，延迟 versions/logout 响应，先复现 documentReads=1（应为0）；
   注销开始时使文件加载代次失效并禁用文件写入后转绿。也验证注销失败后身份保留、
   用户可主动重新打开文件。审查复核无遗留问题。
4. 第二轮浏览器登录后超时，检查发现 C++ 静态白名单未登记既有 `search.js` 及新增
   `processing.js`，导致 app 模块无法加载。补充 socket 用例先复现非200响应，再登记
   两条精确路径。未知 JS 路径仍为404，无目录通配暴露。该问题也说明先前模拟 HTTP
   浏览器测试不能替代真实 C++ 链路；此次没有将旧夹具通过结果当作真实链路已通过。
5. 静态资源 RED 单元误用了 `SMARTDOCS_TEST_FILTER`，实际测试器读取 `TEST_FILTER`
   或 `--filter=`，因此运行了无数据库套件：56通过、1失败、84跳过；失败正是新增
   白名单断言。它不是完整回归或最终成功结果。没有连接生产数据库。
6. 独立审查确认白名单改动及对应 fixture 清理无新增问题。未提交原始浏览器驱动
   日志，以免归档自动化登录参数；仅保留不含凭据的测试输出和界面截图。
7. 第三轮已走通任务操作、真实解析、批准变更及六屏宽检查，但切换 reader 时测试
   误将隐藏的旧账号表格视为新页面就绪，随后元素被正常刷新移除而报 no such element。
   等待条件改为确认新用户名、已登录区域可见且加载结束，再读取文件行；未修改生产
   登录逻辑。这轮仍算失败，未执行后续完整 C++ 回归。
8. 第四轮 reader 隐藏写按钮及后端403验证通过，删除调用按既有契约返回204，但测试
   错用要求200 JSON的辅助函数而失败，导致清理查询已删除文件也收到404。修正为
   检查204，并在删除场景 finally 恢复人工文件以便清理。未修改业务接口返回值。

原始失败输出：[document 字段 RED](document-field-red.txt)、[错误码夹具](failed-fixture-code.txt)、
[缺少静态模块](failed-static-assets.txt)、[静态资源 RED](static-assets-red.txt)、
[切换账号等待](failed-login-wait.txt)、[删除返回值断言](failed-delete-expectation.txt)。
最终整组重新执行成功，不把失败轮次的局部通过数字叠加计入最终结果。

## 边界

这是 M2 的处理状态/人工重试增量，不是 M2 全阶段或完整 P0 验收。浏览器测试使用
真实 C++、临时 MySQL 和 TXT 解析，但向量任务仅验证排队/取消/重试，没有执行模型。
历史版本禁止写入和异步竞态另由 JS/C++ 测试覆盖，不能声称每个分支都经过浏览器。
未重新执行所有 Python、M1 全套浏览器或浏览器直通混合模型链路；无生产常驻部署、
公网/TLS 联调、性能或长期稳定性承诺。后续 M2 综合验收仍需补齐，未进入 M3。
