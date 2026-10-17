# M2 物理页说明与阶段审计

2026-10-18；基线 `329df1df2c43b2659505f85bd50a7a69a17801a7`。仅改现有 PDF
预览输入标签及 PDF 来源面板说明：从 1 开始的物理页码可能不同于印刷页码。
不改变 URL、定位、权限、解析或检索逻辑；TXT/Markdown 保持行范围说明。

## 本轮实测

- TDD：[RED](search-red.txt)9通过/1失败，正是缺少物理页说明；补文案后
  [GREEN](search-green.txt)10/10，页面契约8/8。最初宿主旧 Node 不支持所用
  flag，随后沙箱内 `node --test` 子进程启动失败；均不是业务 RED。实际红绿
  使用既有 `/tmp/smartdocs-safe.h4SzvX/bin/node test/e2e/search_ui_test.mjs`。
  归档仅去除 RED 日志空白行的行尾空格，断言及测试结果原样保留。
- [完整前端回归](frontend-regression.txt)：4个行为文件分别18/1/9/10，共
  **38/38**，契约另 **8/8**；`git diff --check` 无误。这些是受控 DOM 测试。
- [真实页面日志及终态](browser-journal-and-status.txt)：**1/1，92.819秒**，
  不含建库。C++/隔离 MySQL/真实三格式解析/Chrome 沙箱，无模拟 API、无模型。
  验证4处正文和固定版本、TXT/MD行范围、PDF物理页1/2、来源链接及登录原件
  字节/匿名401；PDF说明出现而TXT/MD无该说明。实际HTML的预览标签文本亦通过，
  **标签只做DOM检查，没有据此声称预览面板可见布局或原生PDF渲染通过**。
- 360/768/1280视口的来源面板内部无横向滚动、宽度不大于视口；人工逐张查看
  [360](pdf-page2-source-360.png)、[768](pdf-page2-source-768.png)、
  [1280](pdf-page2-source-1280.png)：物理页说明、原文、ID及原件链接完整，窄屏折行。
  截图只覆盖来源面板，不代表整页或PDF阅读器。审查指出宽度断言不等于面板
  left/right都在视口内，故不额外声称完整视口包含关系；无 Critical/Important。

本轮不改CSS；按移动端技能复用既有折行布局并增加768宽度验证，不引入新组件。
[源码差异与SHA核对](hash-audit.txt)明确区分历史代码证据和本轮新增覆盖。
工作树与执行副本的两项静态资源和浏览器测试逐文件cmp一致；复用既有构建，
server/admin实际哈希与上一轮一致。历史失败记录不删除、不与本轮计数合并。

## 复现及资源

前端在工作树逐个运行以下行为文件，再运行契约检查：

```bash
for f in test/e2e/ui_behavior_test.mjs test/e2e/processing_lifecycle_test.mjs \
  test/e2e/processing_ui_test.mjs test/e2e/search_ui_test.mjs; do
  /tmp/smartdocs-safe.h4SzvX/bin/node "$f" || exit
done
python3 -B test/e2e/ui_contract_test.py
git diff --check
```

真实页面仅在既有限额下执行，执行副本为 `/tmp/smartdocs-safe.h4SzvX/repo`：

```bash
systemd-run --unit=smartdocs-m2-physical-pages \
  --property=WorkingDirectory=/tmp/smartdocs-safe.h4SzvX/repo \
  --property=Slice=smartdocsvectorcheck.slice \
  --property=User=nobody --property=Group=nogroup --property=Nice=10 \
  --property=RuntimeMaxSec=600 --property=TimeoutStopSec=5 \
  --property=KillMode=control-group --property=NoNewPrivileges=no --property=UMask=0077 \
  --setenv=PYTHONPATH=python \
  --setenv=SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome \
  --setenv=SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver \
  --setenv=SMARTDOCS_SOURCE_BROWSER_OUTPUT=/tmp/smartdocs-safe.h4SzvX/physical-page-output \
  bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_sources_browser_test.py
```

共享slice保持MemoryHigh=1200MiB、Max=1600MiB、Swap=0、单核、Tasks=256、磁盘
读写各5MB/s及200IOPS。NoNewPrivileges=no仅保留既有Chrome setuid沙箱，不用
--no-sandbox，不覆盖HOME。主机可用内存低于512MiB即停止；本次未触发。
[离散采样](resource-samples.txt)不是峰值测量；high起止80269，max/oom/oom_kill=0。
最终exit0、MainPID0、无运行smartdocs测试单元，原MySQL active，可用内存1855MiB。
夹具清理仅本次临时人工数据库/存储，无恢复副本，原库不变。没有读取/归档密钥
或driver.log，没有远程API调用，没有安装生产服务。

[阶段审计](../stage-audit.md)已识别剩余中文PDF与纯图片扫描PDF验证，M2仍未关闭。
