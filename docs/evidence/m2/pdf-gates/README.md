# M2 中文 PDF 与纯图片扫描件补验

基线 `f43895868212de535726d36bb0c6177194216e79`，2026-10-18（北京时间）。
本增量仅新增人工夹具和加强既有测试，生产解析、业务、UI、向量实现均未改动。
[夹具来源、内容及字体声明](../../../../test/fixtures/m2/README.md)随代码提交。

## 实际结果

- [完整 Python + worker 日志](python-worker-journal.txt)：Python **104/104**，
  21.248秒；真实 worker **7/7**，13.742秒，不含建库。7个测试中新增中文PDF
  和扫描件子场景，不虚增为更多独立测试。schema5及103文件迁移检查通过。
- [真实浏览器](browser-journal.txt) **1/1**，54.461秒，不含建库；真实C++/
  MySQL/解析器/Chrome通过全部六处来源，包括中文两页、各行1–2、固定版本
  链接与page=1/2、登录态原件字节一致和匿名401。没有模型或模拟API。
- 人工检查六张实际页面截图，英文和中文各360/768/1280。中文示例见
  [360](chinese-page2-source-360.png)、[768](chinese-page2-source-768.png)、
  [1280](chinese-page2-source-1280.png)：文字、物理页说明、ID和原件链接完整。
  仅来源面板截图；预览标签仅DOM检查，未重跑原生PDF阅读器渲染。
- [源码/二进制SHA](source-sha256.txt)及执行副本cmp一致；
  [历史一致性](historical-consistency.txt)中常驻核心9项哈希全部一致。
  `git diff --exit-code 729d2b5 -- code db Makefile test/integration test/unit scripts`、
  `git diff --exit-code 4adaec5 -- python`、`git diff --exit-code f438958 -- resources`
  均无差异。历史C++141/141、前端38/38、真实模型组合等仍按原轮次保留，
  不伪称本轮重新执行；本轮只新增测试/夹具/文档。
- 独立只读审查无Critical/Important。生成依赖版本和PNG证明边界均已记录，
  不声称跨版本逐字节复现。两项PDF关闭缺口得到对应真实结果，不以审查代替实测。
- [终态](final-status.txt)：两个单元exit0、inactive/dead、MainPID0，无运行中的
  smartdocs测试单元，原MySQL active，主机可用内存1872MiB。临时人工数据库和
  存储由夹具清理，无恢复副本，不影响原库。无远程API调用或生产部署。
  [采样](resource-samples.txt)high起止80269、max/oom/oom_kill均0；离散采样不是
  峰值或长期稳定性测量。原有混合组合的失败/资源风险并未被本轮无模型结果抹去。

## 测试与判断依据

先写独立预期的测试，再生成夹具。属于已有功能的覆盖补全，不声称生产缺陷
RED/GREEN；最初17项PDF定向测试全部通过，没有为通过改变解析输出或放宽断言。
新增测试能捕获中文丢失/替换、字节偏移冒充字符偏移、物理页编号错误、纯图片
被标为解析完成等回归；预期不来自被测解析器或生成器函数。

- 中文两页各两行，精确正文分别为“部署说明\n数据库备份”和
  “恢复步骤\n验证数据完整性”，Unicode字符范围[0,10)、[0,12)，物理页1/2。
- 扫描样例确有绘制Image XObject的内容操作，不是空白页；没有可提取文字，
  受限解析返回 `no_extractable_text`。这是人工图片模拟扫描件，不承诺OCR。
- 真实 worker 沿用上传→领取→受限子进程→C++发布→MySQL检索路径，继续验证
  片段完整字段、SHA、固定版本历史引用及跨版本拒绝；额外检查目标命中数量，
  避免字典比较掩盖重复结果。扫描件要求failed、准确错误码、零片段，仍可通过
  元数据发现并鉴权下载原始字节。
- 浏览器测试复用相同夹具，截图名包含样例名，英文与中文PDF证据互不覆盖。
  源绘图PNG不作为独立PDF渲染证据；页面检查只证明来源面板与固定版本URL。

## 执行方式

所有数据库/浏览器验证在当前ECS、nobody/nogroup、Nice=10、UMask=0077下串行，
工作目录 `/tmp/smartdocs-safe.h4SzvX/repo`。共用原smartdocsvectorcheck.slice：
MemoryHigh1200MiB、Max1600MiB、Swap0、CPU100%、Tasks256、/dev/vda读写各
5MB/s和200IOPS；主机可用内存低于512MiB时停止。不扩容、不清页缓存、不并发
模型，也不读取DeepSeek env或driver.log。

`smartdocs-m2-pdf-gates.service`：RuntimeMaxSec600、TimeoutStopSec5、
KillMode=control-group、NoNewPrivileges=yes、PYTHONPATH=python，执行：

```bash
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
# 仅前一项成功后：
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_worker_test.py
```

浏览器沿用既有Chrome和driver路径、PYTHONPATH=python、
SMARTDOCS_SOURCE_BROWSER_OUTPUT=/tmp/smartdocs-safe.h4SzvX/pdf-gates-browser-output；
仅在上一个单元完全退出后执行相同受限MySQL脚本，Python入口改为
`test/e2e/m2_sources_browser_test.py`。NoNewPrivileges=no保留Chrome沙箱，其余
上限相同。不使用--no-sandbox，不改变HOME。

本轮不重新编译或运行无变化的完整C++套件、真实模型组合；不扩大历史分母。
不包含公网/TLS、生产常驻部署、吞吐或长期稳定性验收。
