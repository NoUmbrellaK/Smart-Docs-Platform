# M2 短生命周期分词后的浏览器组合复验

2026-10-18，北京时间；被测实现 `4adaec5cb16816ee408e292868e2c6699743c3e4`。
原浏览器测试、业务断言、模型修订及资源配额不变，M2 尚未验收。

## 原用例：0/1，失败位置已后移

[首次原始日志](initial-journal.txt)：模型实际 READY 后启动隔离 MySQL/C++/网关/
Chrome 组合，schema 5 和 103 文件升级检查通过。Chrome 创建会话成功，真实获批
资料向量发布成功，内部资料不向量化；登录后执行首次中文混合搜索。
测试在 `hybrid_browser_test.py:135` 读取搜索状态时，WebDriver `/execute/sync`
HTTP 请求超时；测试本体 208.715 秒，0/1，单元退出码 1。

没有走到结果数量、向量命中、固定版本来源、截图、新版与撤销断言。
不能把“向量发布成功”当成“页面检索通过”，也不能把这次 WebDriver 超时
写成已证实的 embedding_timeout。原例的一次人工重试分支未触发。
失败后编码服务仍 active，显式停止后 exit 0、MainPID=0；原 MySQL active。
没有读取可能含登录材料的 driver.log，没有成功截图。

## 分层计时诊断

[trace_combination.py](trace_combination.py)仅用标准库包装原用例的三个实际入口：
Browser.request、vector_worker.request_encoding、search_http.search_documents。
记录固定阶段、begin/end、耗时和 ok/error/timeout，不输出参数、结果、异常文本，
不改超时、重试或业务断言；浏览器只记录慢于 1 秒的控制调用及异常。
`EmbeddingError` 归为通用 error，不单凭该字段推断具体编码错误。
脚本 self-test 验证返回对象与异常对象保持不变，敏感示例未进入输出；独立审查
未发现 Critical/Important。诊断有轻微观测开销，不能回填为首次运行的阶段实测。

诊断轮最终 **1/1 通过**，测试本体 194.151 秒、单元 exit 0，见
[完整计时日志](trace-journal.txt)。开始时 browser_control 的四次 0 秒 error
是原 Browser.start 等待驱动就绪时可捕获的连接失败，不是四次业务测试失败。
创建浏览器会话 63.652 秒；资料编码 30.012 秒；首次查询编码 7.787 秒，
整个 search_coordinator 17.058 秒。之后 encoding 的即时 error 来自原用例
主动切换不存在 socket 的降级场景，协调层仍正常返回并通过关键词降级断言。

本轮真实断言覆盖：中文语义问题命中英文资料且 match_sources 为 vector；
内部资料排除；原文、version_id、Lines 1–1 及固定版本内容链接正确；
编码不可用明确关键词降级；上传新版后旧正文退出默认搜索、旧出处仍指向旧版；
元数据发现未解析新版；本地关键词返回新版而非旧版；新版批准不继承、批准后
进入获批范围、撤销后再次排除。没有把缺失向量的降级结果算作真实混合召回。

人工审阅两张元素截图：[360 视口](hybrid-real-360.png)、
[1280 视口](hybrid-real-1280.png)。宽图能看到 hybrid、向量命中、文件/版本与行号；
窄图在 1000px 高视口底部截断结果卡，未展示完整出处，不能用它证明窄屏下出处
全部可见。页面来源正确性另由原用例 DOM/URL/正文断言支持。

这两次完整场景结果分别为 0/1 和 1/1，合计 **1/2**，不隐去首次失败。
没有改代码/断言/超时来换取通过，但诊断日志及未受控页缓存等因素意味着
不能将前后差异归因于单一变量，更不能声明冷启动或长期稳定性问题已解决。
此外首次单元显式设置 HOME 为既有测试 home，诊断轮使用 systemd 账号默认值；
两轮 Chrome 都显式使用临时 user-data-dir。未单独验证该环境差异的影响。
本轮已证明原语义闭环在当前服务器/配额有一次实际通过，仍需保留资源风险；
下一步补三格式/双页出处和持久失败→显式重试的 M2 业务证据，不继续盲目重复组合。

## 环境及复现

原共享 `smartdocsvectorcheck.slice`：MemoryHigh=1200 MiB、Max=1600 MiB、Swap=0，
CPUQuota=100%、Tasks=256、/dev/vda 读写各 5 MB/s、200 IOPS，Nice=10。
主机 2 vCPU、3495 MiB、无 swap；仅串行运行一个组合，观察到 MemAvailable
低于 512 MiB 即停止，不提高上限，不清系统页缓存，不安装生产服务。
编码单元 nobody/nogroup、Type=notify、NotifyAccess=main、PrivateNetwork=yes、
RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes、UMask=0077，
TimeoutStartSec=190、RuntimeMaxSec=900、TimeoutStopSec=5、KillMode=control-group。
浏览器单元同账号、同切片、RuntimeMaxSec=780、TimeoutStopSec=5，
NoNewPrivileges=no 保留 Chrome setuid 沙箱，不使用 --no-sandbox。

工作目录 `/tmp/smartdocs-safe.h4SzvX/repo`。模型保持原共享 model.lock，私有
socket 目录 0700/文件 0600；启动并确认 READY 后才运行以下受限测试命令：

```bash
# 环境：PYTHONPATH=python；SMARTDOCS_BROWSER_BIN 指向 chrome-linux64/chrome，
# SMARTDOCS_WEBDRIVER_BIN 指向 chromedriver-linux64/chromedriver；均位于既有临时根目录。
# SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-safe.h4SzvX/resident-combo-socket/encoder.sock
# SMARTDOCS_HYBRID_BROWSER_OUTPUT 使用独立的 transient-combo-artifacts / transient-trace-artifacts。
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/hybrid_browser_test.py
# 诊断轮仅将上面的 Python 入口改为：
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  /tmp/smartdocs-safe.h4SzvX/trace_combination.py /tmp/smartdocs-safe.h4SzvX/repo
```

[源码与执行产物指纹](source-sha256.txt)：列出的 Python/脚本与工作树 cmp 一致。
C++ 源文件/头文件、Makefile 及 resources 也逐文件一致；执行副本
`make -q bin/server bin/smartdocs-admin` 返回 0，沿用已有构建，本轮未重新编译。
工作树 bin 是更早的本地产物，与执行副本不一致，未用于本轮验收，不宣称二进制
逐字相同；记录的是实际被测临时副本的二进制指纹。没有重跑全量 C++/Python/JS。

## 压力和终态

[资源采样](resource-samples.txt)按每段命令顺序记录：时间、两个单元状态、
最近 journal、共享组 memory.current（bytes）、memory.events、memory.pressure、
io.pressure、free -m。PSI 是共享组等待比例，不是整机磁盘利用率；采样不是峰值。
首次 high 从 68978 增至 73229，诊断结束至 77682；max/oom/oom_kill 始终为 0。
两个组合均存在明显内存回收与 IO 等待，不能写成“无资源压力”。
模型各自在组合终态后显式停止；最终所有 smartdocs 运行单元为 0、四个单元
MainPID=0，原 MySQL active，主机 MemAvailable 约 1818 MiB，见[终态](final-status.txt)。
原用例清理临时数据库/浏览器 profile，原模型与业务数据保留。未调用远程 API，
未读取 DeepSeek env，无生产功能改动。M2 其余门槛见[阶段索引](../README.md)。
