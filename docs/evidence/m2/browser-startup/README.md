# M2 浏览器启动负载排查

2026-10-18（北京时间）；基线 `bfe980be58e1d08f89fe108870bfb2b60f480fb6`。
本轮保留的代码变更仅为测试浏览器的空白启动页及其自测，不改变生产代码、模型或索引。
完整 M2 组合验收仍未通过，M3 未开始。

## 已确认与修正

Chrome for Testing 155.0.8059.84 使用现有自定义临时 profile 时，初始实际页面为
`chrome://new-tab-page/`，还存在新标签页 iframe 及两个地址栏 browser_ui target。
原测试在模型加载前停留于该页面，不是真正空白的浏览器。

只增加 `session.restore_on_startup=4`、`session.startup_urls=['about:blank']`。
这是 Chrome 原生启动页偏好，值 4 的语义见
[Chromium 定义](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/chrome/browser/prefs/session_startup_pref.h)；
自定义 profile 不保证自动附加空白页，参见
[ChromeDriver 启动实现](https://chromium.googlesource.com/chromium/src/+/main/chrome/test/chromedriver/chrome_launcher.cc)。
本轮以实际浏览器结果为准，不只依赖源码推断。

- 新增 session 配置边界自测先因 `None != 4` 失败，修正后 **13/13** 通过：
  [红灯](self-red.txt)、[绿灯](self-green.txt)。最初测试夹具曾因沙箱禁止占用端口报错，
  将与该配置断言无关的空闲端口分配替换后，才得到上述有效红灯。
- 真实 Chrome 旧配置的空白页断言失败；新配置通过，初始 page target 为
  `about:blank`，无 New Tab iframe。见[旧配置](startup-red.txt)、[新配置](startup-green.jsonl)。
- 三个 renderer 仍存在，两个地址栏 browser_ui target 也仍存在，没有禁用它们。
  renderer 快照 Seccomp=2；未添加 `--no-sandbox`、单进程模式或关闭站点隔离。
  PDF 内置查看、性能日志、导航/编码时限均未改变。

两次启动快照的进程 PSS 合计为 396380/376691 KiB，组 memory.current 为
294334464/201515008 bytes；这些数值包含诊断 Python、driver 和 Chrome，采样非原子，
并受缓存归属和启动时刻影响。不是稳定内存节省、峰值或性能基准，也不能把多个
进程的 RSS 直接相加视为物理内存。诊断目的在于确认并移除无关新标签页负载。

## 两次组合实验：均未通过

1. **保留版本：仅空白启动页**。00:11:23–00:15:45，测试 **0/1**、171.241 秒、
   单元退出码 1。schema 5 和 103 文件升级夹具通过；首次编码持久 timeout，唯一
   显式 API 重试成功，批准版本的真实向量已发布，内部版本未向量化。随后首次
   打开 `/app.html` 的 WebDriver HTTP 请求触发原 30 秒超时；代理写响应时出现
   BrokenPipeError。未登录、未到达搜索断言，不能据此判定具体哪个页面资源变慢。
   详见[完整输出与服务终态](blank-combination.txt)。
2. **未保留的顺序实验**。将同一导航及 idle 断言移到模型加载前，没有删除断言、
   改超时或增重试。页面正常就绪，但首次编码和唯一重试均 `embedding_timeout`；
   **0/1**、210.588 秒、退出码 1，00:17:23–00:22:20。见
   [输出](ui-first-check.txt)、[服务状态](ui-first-status.txt)。顺序调整并未解决整体
   问题，已从工作源码及测试副本撤回；仅留[实验 diff](ui-first-experiment.diff)。

不能把第一轮的向量发布与第二轮的页面就绪拼成一次成功验收。两轮都没有成功截图。
最终 `hybrid_browser_test.py` 与基线完全一致，所保留代码对应第一轮失败场景；没有
把第二轮结果充作最终版本通过的证明。实验版本的 hybrid 测试 SHA-256 为
`a158c9d744732a5225ec729901bf061811e50d25dbd447d1260843836e71170e`，
最终源码另见[哈希](source-sha256.txt)。

## 限额、复现与清理

沿用[前轮原配额](../arena-browser-recheck/README.md)：当前 2 vCPU / 3495 MiB ECS，
无 swap；nobody/nogroup、Nice=10；共享 MemoryHigh=1200MiB、MemoryMax=1600MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，/dev/vda 读写各 5MB/s、200IOPS。
每次只有一个重型场景。编码服务禁网、AF_UNIX、NoNewPrivileges，目录 0700、socket
连接前确认 0600，复用模型锁，正式独立编码子进程；没有使用预热/fork 原型。

两轮组合均使用新鲜编码服务 RuntimeMaxSec=900，测试单元 780 秒；编码请求仍 60 秒。
编码服务在各轮测试结束时均存活，随后显式停止，故失败不是服务总寿命到期。
首次组合的参数与前轮一致，只换为 `blank-browser.sock` 和 `blank-browser-artifacts`；
顺序实验使用 `ui-first.sock` / `ui-first-artifacts`，精确环境及命令在服务状态中。

[诊断脚本](browser_memory_probe.py)仅用于当前受限验收环境，无登录、数据库或模型。
使用原轻量 Python、环境变量指定 Chrome/driver，在同资源组的非 root 单元运行：

```bash
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B /tmp/smartdocs-safe.h4SzvX/browser_memory_probe.py about:blank
```

诊断单元最多 180 秒。旧配置红灯需要基线版 `m1_browser_test.py`；不能在当前修正后
配置上称为旧配置对照。脚本仅读取自己 cgroup 的进程状态/PSS与浏览器 target，不
归档完整进程参数或登录日志。driver.log 不提交。

memory.high：39710→43785（空白启动页组合）→47501（顺序实验）；max/oom/oom_kill
始终 0。最后确认本轮服务 MainPID=0、无运行中的 smartdocs 测试服务，原 mysql active；
见[最终清理](final-status.txt)。无远程模型 API 调用，未读取 DeepSeek env、未下载安装依赖，
未重跑完整 M1/C++/Python/JS 套件。启动自测不等于 PDF 浏览器回归。

本轮证明了一个测试启动缺陷并移除了无关页面，但无法证明组合资源适配已解决。
后续停止加载顺序试错，评估编码器整体常驻内存及重复加载方式，或更低资源的真实
向量实现；任何选型变更都需重新验证输出、索引修订及检索质量，不能用假向量、
放宽限额或关键词降级代替 M2 验收。
