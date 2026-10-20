# M5 当前文件工作台浏览器复验

2026-10-21，源码基线 `9d35e48`，复用已有普通版 server/admin、Chrome/ChromeDriver
155.0.8059.84 和既有 M1 用例。无产品代码、测试断言、超时或资源上限修改，
没有构建、安装依赖、读取真实 key、调用模型或操作业务数据库。

## 结果与失败分母

| 运行（北京时间） | 实际结果 | 证据 |
| --- | --- | --- |
| 首轮 00:41:43–00:43:48 | HTTP 9/9、44断言；浏览器 `TimeoutExpired`，未产出有效成绩 | [日志](initial-journal.txt)、[运行限额](initial-running.txt) |
| 单独版本诊断，00:44:36结束 | Chrome/driver 的 `--version` 分别6.199/2.402秒完成；独立探测给定60秒，不是浏览器场景通过 | [日志](dependency-journal.txt) |
| 第二轮 00:45:03–00:47:54 | HTTP 9/9、44断言；浏览器 `TimeoutError`，无有效成绩 | [日志](recheck-journal.txt)、[运行限额](recheck-running.txt) |
| 第三轮，仅加分层计时，00:49:05–00:51:14 | HTTP 9/9、44断言；浏览器 **2/2、20断言** | [日志](diagnostic-journal.txt)、[HTTP](http.json)、[浏览器](browser.json) |

三次浏览器尝试合计 **1成功/2失败**。HTTP重复准备不累计成27个不同场景；
13项浏览器工具自测另见[self-test.txt](self-test.txt)，不计为真实页面场景。
`browser.json` 的 `schema_version=2` 是结果格式版本，数据库实际迁移至16。

成功轮覆盖：

- 刷新后保持已确认part0；错选文件不发PUT，正确重选只上传part1/2；
  完成后的文件、版本、处理任务ID稳定，下载SHA与源文件相等。
- 受保护的固定版本PDF：已登录读取200、匿名401；page=1/2均有实际正文渲染，
  不是仅检查工具栏或URL；截图字节和文档视口指纹不同。已目视核对
  [第一页红色正文](browser-artifacts/pdf-page-1.png)、
  [第二页蓝色正文与黄色块](browser-artifacts/pdf-page-2.png)。

## 诊断结论与限制

首轮未创建 `browser-artifacts`，结合 `m1_browser_test.main()` 的调用顺序，
故障位于输出目录创建前的依赖版本探测；现有日志不能分辨哪个二进制超时。
第二轮目录已创建但无截图，只能说明通过依赖探测，不能证明是浏览器启动超时，
也不能排除后续WebDriver或直接HTTP请求超时。

第三轮包装 `Browser.request`，只记录方法、白名单操作名、耗时和异常类型，
调用原实现不变；顶层失败诊断仅输出文件名、函数及行号，不输出请求正文、
cookie、会话标识或异常消息。[完整诊断调用](diagnostic-invocation.txt)
与[原始调用](invocation.txt)保留，方便对照。
第三轮通过不能恢复第二轮丢失的异常位置，也不能证明冷加载/IO就是根因。
日志中的首次 `GET status ConnectionRefusedError` 被原有就绪轮询捕获，随后就绪、
创建会话2.843秒成功；单个 `WD_FAIL` 不等于整轮失败。
**冷启动及长期稳定性问题仍未解决**；下次出现时保留此分层诊断，不盲目提高超时。

## 环境、安全与复现

[164个实际运行源码/资源比对及二进制哈希](sources.txt)绑定当前工作树与
`/tmp/smartdocs-safe.h4SzvX/repo`；自测直接运行工作树版本，未使用旧安全副本自测。
各轮使用新的临时MySQL数据库、合成fixture、临时账号、存储和Chrome配置。
正常server不是故障注入版。浏览器非root且沙箱启用。

三个测试unit均由 `scripts/test-with-mysql.sh -- /usr/bin/python3 -B -c`
执行对应调用文本；工作目录为上述受限副本，Chrome环境变量指向
`/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome` 和
`/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver`。
复现时应新建私有输出目录并替换调用中的output路径，不能依赖临时目录长期存在。

[诊断轮运行属性](diagnostic-running.txt)与[共享slice限额](slice-limits.txt)：使用 `smartdocsvectorcheck.slice`，
CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、TasksMax256，
磁盘读写各5MB/s和200IOPS；非root、NoNewPrivileges、仅loopback网络，
总运行900秒、停止等待5秒、KillMode=control-group。未并行启动其他重任务。
独立版本诊断使用同一slice、禁止全部IP、150秒总限时。

[终态检查](final-status.txt)：成功轮退出0，前两轮退出1且保留failed状态，
无nobody测试进程；原MySQL仍active。memory.events的high仍80521，
max/oom/oom_kill均0，即memory.high事件未增加、OOM计数为0；不表示CPU/IO未受配额节流。
终态可用内存见原始记录，
不作为峰值性能结论。临时运行目录与隔离库由原有清理逻辑回收。

本增量补足当前文件工作台的既有两项浏览器场景，不是全站浏览器回归、
固定30项业务评测、模型质量、吞吐、整机断电、生产部署或完整业务演示。
M5及整个P0仍未完成。

归档后再次调用现有 `validate_browser_evidence`，结构、断言总数、截图字节与哈希
校验通过；再次运行 `nice -n 10 python3 -B test/e2e/m1_browser_self_test.py`
13/13通过（1.308秒）。调用文本通过Python语法编译；文档相对链接及
`git diff --check`通过。以上是归档复核，不额外计入浏览器尝试或业务分母。
