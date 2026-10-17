# M2 三格式解析与固定版本出处

2026-10-18，北京时间；基于 `f4784269eb0ddc80388da7795e6608ca644feeac`，
只增强测试和归档证据，不修改生产代码，不调用远程 API 或启动本地向量模型。
M2 仍未验收。

## 真实 worker

[完整日志](worker-journal.txt)：**7/7 通过**，测试本体 13.802 秒；
隔离 MySQL 初始化、schema 5 和 103 文件迁移检查另计。
通过真实上传、worker CLI、C++ HTTP、MySQL ngram 索引核对：

- TXT：UTF-8 BOM、CRLF、中文及 emoji，规范化原文为第 1–2 行。
- Markdown：CRLF 和空行，规范化原文为第 1–3 行。
- 两页 PDF：分别得到 PAGE ONE / PAGE TWO，物理页码 1 / 2，每页第 1 行。
- 四个片段的 file_id、version_id、原始字节 SHA、parser_revision、chunk_id、
  Unicode 字符偏移、行范围及 PDF 页码逐字段断言；关键词命中片段与来源完全一致。
- 对三种格式分别上传相同字节的新版本，旧片段保持旧版且标记 historical；
  用旧 chunk_id 访问新版路径返回 404。这验证版本身份隔离，不代表正文改变场景；
  改变正文的新版排除见[既有组合](../transient-browser/README.md)。
- 其余原有六项回归仍执行：大正文 500 片段分页、无法解析资料仍可管理/下载、
  worker 锁及中断恢复、取消/过期 attempt、C++ 重启后人工重试、内部通道鉴权。

## 浏览器首次运行及测试假设修正

[首次日志](browser-initial-journal.txt) **0/1**，测试本体 50.367 秒。
TXT 的正文、行号、固定版本链接、原始字节、匿名拒绝已通过；随后 Markdown
搜索返回两张卡，测试错误地要求整个项目恰有一张结果卡，因此中止，未运行 PDF。
后端 `document_search.cpp` 使用 `MATCH ... IN NATURAL LANGUAGE MODE`，不是精确词匹配；
已有 worker 用例也是先按 file_id 筛选目标结果再比较片段。

修正仅限浏览器测试：先按 DOM 中精确 file_id/version_id 找目标卡，要求目标
片段数量准确且每段文本唯一，再逐一点击验证。记录总卡数和目标卡数；生产
检索、数据、超时和来源断言未更改。不以扩大预期数量或跳过 Markdown 换取通过。
初版 browser 测试 SHA 为 `b80a5f017c0de1790c527064db2048d381635abbd9a4884926faac287df861ba`。

修正后[完整日志](browser-final-journal.txt) **1/1 通过**，测试本体 8.641 秒，
两轮合计 **1/2**，不隐去首次失败。Markdown 仍返回 2 卡，目标卡为 1，确认
测试假设修正与实际结果一致；TXT 为 1/1，PDF 为 2/2（总卡/目标卡）。真实
worker→MySQL/C++→Chrome 中四处正文、file/version、行范围、PDF 1/2 页及
`/versions/{version}/content#page=N` 均通过；读取页面显示链接的 HTTP 部分，
登录态取回字节与上传原件一致，匿名请求返回 401。没有合成 API 响应。

人工查看 [1280 视口出处截图](pdf-page2-source-1280.png) 与
[360 视口出处截图](pdf-page2-source-360.png)：两图均完整显示来源面板的
file/version、chunk、PDF page 2、Lines 1–1、PAGE TWO 和原件链接；窄图长 ID
折行。它们只截取来源面板，不代表整页布局或 PDF 阅读器渲染。

## 复现与边界

执行副本 `/tmp/smartdocs-safe.h4SzvX/repo`；Python 使用既有 parser venv。
两个单元串行，nobody/nogroup、Nice=10、UMask=0077、RuntimeMaxSec=600、
TimeoutStopSec=5、KillMode=control-group；共用既有 smartdocsvectorcheck.slice：
MemoryHigh=1200 MiB、Max=1600 MiB、Swap=0、CPUQuota=100%、Tasks=256，
/dev/vda 读写各 5 MB/s、200 IOPS。观察到主机 MemAvailable <512 MiB 即停止。
worker 单元 NoNewPrivileges=yes，Chrome 单元为 no 以保留既有 setuid 沙箱；
浏览器断言 sandbox=enabled，不使用 --no-sandbox，不覆盖 HOME。

```bash
# 在上述受限单元内执行；PYTHONPATH=python。
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_worker_test.py
# 上一单元完全退出后：
# SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome
# SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver
# SMARTDOCS_SOURCE_BROWSER_OUTPUT=/tmp/smartdocs-safe.h4SzvX/source-browser-output
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_sources_browser_test.py
```

[源码及二进制指纹](source-sha256.txt)。执行副本与工作树 C++/头文件、Makefile、
resources、Python 源码和既有测试逐文件 cmp 一致；新增浏览器测试另行同步。
执行副本 `make -q bin/server bin/smartdocs-admin` exit 0，沿用已有构建；未使用
工作树中较早的 bin，也未重编译或宣称重跑完整 C++/Python/JS 回归。
临时库由测试脚本创建并清理，未使用原业务库；未读取或归档 driver.log/密钥。
只读审查无 Critical/Important。worker 的结果字典比较不另证明无重复命中；
该微小覆盖限制不影响来源身份/版本断言，本增量不声称检索去重已获新增证明。

[资源采样](resource-samples.txt)不是连续监控或峰值测量。共享组 high 起止均
77682，max/oom/oom_kill 均 0；未新增回收限额事件，不代表彻底消除历史 IO 压力。
[终态](final-status.txt)：三单元 MainPID=0，没有 smartdocs 运行单元；初版失败
状态保留，worker/最终浏览器 exit 0；原 MySQL active，主机可用内存 1861 MiB。

本增量不包含新的 Chrome 原生 PDF 画面渲染检查，不能以页锚点链接断言替代
实际渲染；此前双页预览证据见 M1 验收。来源 UI 目前显示 `PDF page N`，尚需按
R-01 明示“从 1 开始的物理页码，与印刷页码可能不同”。另须补常驻模型服务
请求中断→无部分发布且持久失败→重启→显式人工重试的真实业务联合验证。
