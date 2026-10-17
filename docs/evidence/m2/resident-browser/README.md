# M2 常驻模式原浏览器组合与启动隔离诊断

2026-10-18（北京时间），被测提交 `5e188d66f3ae765ab861a2268cecc34cde6a26a3`。
本轮不改生产代码、原浏览器用例、来源/新版/外发断言或超时。
M2 仍未验收，不进入 M3。汇总门槛见[阶段索引](../README.md)。

## 原组合：0/1，尚未编码

新鲜 `smartdocs.resident_service` 使用真实 systemd `Type=notify`，02:02:10
收到模型加载后的 READY，才在 02:02:22 启动原临时 MySQL 包装及
`test/e2e/hybrid_browser_test.py`。schema 5 与 103 文件升级夹具通过。
测试本体运行 103.645 秒后，在第 97 行 `browser.start()` 的创建会话请求失败：
`WebDriver session not created`，单元退出码 1。
详见[原始 journal](combination-journal.txt)。

未执行 worker 编码、登录、混合搜索、固定版本来源、新版或撤销断言；没有成功截图。
没有触发编码器重试。编码服务在失败后仍正常运行，于 02:06:46 显式停止，
退出码 0，因此这次不是编码服务总寿命到期。
原始浏览器错误被现有安全包装缩短，不能仅凭此确定 Chrome 内部失败原因；
没有读取可能包含登录材料的 driver.log。

原用例 SHA-256：`19e06e7b8e0570acd849c67558b2bb425c95c3cd311ceb5d8f1c39ac6b93541c`。
执行副本与工作树逐文件一致，见[源码指纹](source-sha256.txt)。
此次结果不能与旧轮次的页面或向量成功拼接为通过。

## 启动隔离诊断

[startup_probe.py](startup_probe.py)复用原 Browser 及既有
[内存探针](../browser-startup/browser_memory_probe.py)，仅打开 `about:blank`，
不登录、不使用业务页面、不启动临时数据库。只在 WebDriver 启动 HTTP 失败时
打印有界错误说明，仅读取本诊断组内进程内存/沙箱状态；不读 driver.log。
诊断包装不得用于带凭据或业务正文的场景。

首次探针因执行副本缺少内存探针文件而 `FileNotFoundError`，未启动 Chrome；
补齐已有文件后才执行有效诊断，未当作产品失败或重跑完整组合。

单浏览器诊断通过：45.217 秒创建会话、初始页和再次导航均为 `about:blank`，
46.312 秒完成回收、退出码 0。renderer 的 Seccomp=2，没有关闭沙箱或 PDF。
见[原始输出](browser-only.txt)。结束后共享组 memory.high=66085、max/oom/oom_kill=0。
页缓存未受控，不将这轮耗时作为冷启动 SLA 或与其他轮作严格性能对比。

随后同限额启动新鲜模型，实际 READY 后运行相同探针，不启动临时数据库。
模型加空白浏览器也通过：67.962 秒创建会话，71.829 秒回收，退出码 0，
见[输出](model-browser.txt)。模型加载到探针结束 memory.high 66085→68006
（+1921），无 max/oom/oom_kill。02:12:02 的[资源样本](model-browser-pressure.txt)
显示 IO PSI some avg10=97.40%、memory PSI some avg10=78.49%、CPU PSI some
avg10=0.34%；两个 Chrome 进程在 D 状态等待读取页，模型子进程 RSS 622228 KiB。
这些是共享组压力，不等于整台 ECS 97% IO 使用率。探针 JSON 的 group_bytes
只包含浏览器子组，不含另一个单元中的模型，不能当作两者总内存。

**结论边界：**Chrome 沙箱/空白页配置可以正常工作；常驻模型也能与空白浏览器
共存，但共享组产生显著回收和 IO 等待。完整组合额外包含临时 MySQL、C++ 和网关，
其启动失败的具体 Chrome 错误仍未知。这两次隔离测试没有复现失败，不能宣称
“必定是某个组件”或“已经修好”，也不能直接认定必须物理扩容。
下一步先量化模型构造后保留的匿名内存与实际存活对象，评估是否有可回收的
加载期内存；只做有界隔离测量，不继续原样重跑组合，不牺牲完整性校验或向量指纹。

## 原限额与命令

当前 ECS 2 vCPU、3495 MiB、无 swap；共用 `smartdocsvectorcheck.slice`，
MemoryHigh=1200 MiB、MemoryMax=1600 MiB、SwapMax=0、CPUQuota=100%、
TasksMax=256、Nice=10，/dev/vda 读写各 5 MB/s、200 IOPS。没有提高任何限额。
运行时设置主机 MemAvailable 低于 512 MiB 即停止的观察护栏。本次未触发。
组合结束后启动隔离诊断的归档终态显示 memory.high=66085、max/oom/oom_kill=0；
没有归档组合每次资源采样，因此不把口头样本范围当作可复核峰值或最低值。

模型单元 nobody/nogroup，PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、
NoNewPrivileges=yes、UMask=0077，socket 私有目录 0700、文件 0600；
TimeoutStartSec=190、TimeoutStopSec=5、RuntimeMaxSec=900。浏览器单元同账号和
同一共享切片，NoNewPrivileges=no 以保留 Chrome setuid 沙箱，RuntimeMaxSec=780。

```bash
# 以下须在上述受限临时单元执行，按 READY 顺序启动，不直接在 root shell 跑负载。
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m smartdocs.resident_service \
  /tmp/smartdocs-safe.h4SzvX/resident-combo-socket/encoder.sock \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock

SMARTDOCS_BROWSER_BIN=/tmp/smartdocs-safe.h4SzvX/chrome-linux64/chrome \
SMARTDOCS_WEBDRIVER_BIN=/tmp/smartdocs-safe.h4SzvX/chromedriver-linux64/chromedriver \
SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-safe.h4SzvX/resident-combo-socket/encoder.sock \
SMARTDOCS_HYBRID_BROWSER_OUTPUT=/tmp/smartdocs-safe.h4SzvX/resident-browser-evidence \
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/hybrid_browser_test.py
```

执行工作目录 `/tmp/smartdocs-safe.h4SzvX/repo`，保留原模型锁。诊断沿用相同浏览器
环境，命令换为 `python -B /tmp/smartdocs-safe.h4SzvX/resident-browser-probe/startup_probe.py`，
单元总寿命 180 秒；模型加浏览器的启动对照模型总寿命 420 秒，不运行业务请求。
不清空系统页缓存、不调用 DeepSeek、不读取其 env，不安装生产服务。

## 清理及剩余门槛

模型对照单元 02:12:30 显式停止，退出码 0、MainPID=0；本轮所有 smartdocs
测试运行单元为 0，原 MySQL active，主机 MemAvailable 1906476 KiB。
详见[终态](final-status.txt)。共享组最终 high=68006，max/oom/oom_kill=0。
原模型、原业务数据均保留；临时隔离数据库/浏览器 profile 由既有夹具清理。

本轮仅新增诊断与文档，不重跑未修改的全量 C++/Python/前端套件。
完整组合仍 0/1，两个启动隔离诊断不是业务验收分母。
独立只读审查指出部分资源数字缺少可追溯归档；已删除未归档的精确样本，
为保留的 PSI 输出补充采集命令及字段顺序，复核无剩余 Critical/Important。
另据只读 M2 审核，当前 worker 的三格式真实输出仍需永久归档，PDF 两页/文本行
与版本出处需补强，新常驻服务请求中断→持久失败→重启→显式重试的联合场景
仍未完成；见阶段索引，不因单独模型/协议测试通过而关闭这些门槛。
