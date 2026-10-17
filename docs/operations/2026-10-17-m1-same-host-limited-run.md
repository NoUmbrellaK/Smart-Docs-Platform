# 当前 ECS 上的受限 M1 验收方案（2026-10-17）

## 状态与证据

用户要求必须使用当前服务器。重新检查后，本机具备 cgroup v2 和
systemd 249，可以给完整验收进程组施加资源限额，不必先物理扩容。
这不代表已经完成 M1，也不保证限额能够消除全部宿主机风险。

本次检查和预检实际完成了：

- 宿主机约 2 vCPU、3495 MiB 内存、无 swap，开始检查时可用内存
  2347 MiB，根盘剩余约 14 GiB，CPU 空闲，内存与 I/O PSI 为零。
- 临时 systemd 探针以 `nobody` 运行。内核读回的 `memory.max` 为
  `1677721600`，`cpu.max` 为 `100000 100000`，磁盘 `252:0` 的
  `io.max` 为读写各 `5000000` bytes/s、各 `200` IOPS。这里的磁盘
  映射只适用于本机，换环境必须重新检查。
- 从 Google 官方 Chrome for Testing 下载匹配的 Chrome 和 ChromeDriver
  `155.0.8059.84`，仅解压到独立临时目录，动态库检查未发现缺失。
- 在相同 CPU/I/O 限额、非 root、浏览器沙箱启用的条件下，真实浏览器
  打开本地 `data:` 测试页并检查标题成功。该探针没有执行 M1 业务场景。
- 原驱动创建会话的 30 秒等待曾超时；只将探针等待改为 120 秒后，
  浏览器检查在 35.01 秒内成功。提交
  `b3b0a51f5671258912811fd13fa65489a54dc1b5` 因此仅为创建会话放宽等待，
  普通操作仍为 30 秒，超时不会自动重试。回归检查先失败，再修复通过；
  `python3 -B test/e2e/m1_harness_self_test.py` 为 23/23 通过。

首次启动申请曾被自动审批审查拒绝。用户随后明确接受剩余风险并要求继续，
已于 2026-10-17 14:45:23–14:56:34（北京时间）完成第一轮受限运行：

- 被测干净提交为 `a4b69e53591e7603315be103f3981733d762bc0d`；四个 C++
  目标完成干净构建，隔离 MySQL 正常启动。
- 驱动自测 23/23；C++ 87/88、失败 1、跳过 0；UI 契约 8/8。
- C++ 唯一失败是默认配置测试继承了验收环境的线程数 4，却断言默认值 6。
  修正仅使配置测试夹具清除并在结束时恢复全部可选配置环境变量。
- systemd 使用 `/usr/bin/node` 12.22.9，UI 行为测试在解析 `??` 时失败。
  已将机器现有 Node 24.18.0 的可执行文件复制到独立工作区的 `bin/`，
  后续通过显式 PATH 选择，不替换系统 Node。驱动增加了构建前的 Node
  能力检查，确认 `node:test`、全局 `File` 和项目 ES 模块可加载。
- 运行器以状态 1 拦截后续 HTTP、浏览器业务、流式和中断阶段，没有生成
  新的完整证据；测试进程已退出，原 MySQL 服务保持 active。
- 采样期间测试组内存最高约 802 MiB，宿主机可用内存最低约 1880 MiB；
  观察到的 cgroup `high/max/oom/oom_kill` 计数均为 0。这些是离散采样，
  不是连续采集的绝对峰值，也不是服务自身 RSS 指标。

修正后的聚焦验证：5 项配置测试在 14 个可选配置均被故意污染时全部通过；
非 root、显式 Node 24 的 UI 行为测试 18/18；驱动自测 24/24。
真实系统 Node 12 在新前置检查中立即返回 `unsupported_node_runtime`，
未进入构建或 MySQL；Node 24 能力检查通过。

第二轮在干净提交 `3f4129a5ec5c442cfbdafe2f3da8fcf843a4914f` 上执行，
时间为 15:01:25–15:13:58（北京时间）。C++ 88/88、UI 契约 8/8、
UI 行为 18/18、HTTP 9/9（44 断言）全部通过，失败和跳过均为零；
随后浏览器 PDF 第 2 页渲染断言失败，流程以状态 1 退出，未进入流式或
二十轮中断测试。本轮仍没有完整通过证据。

只复用已构建二进制的受限定向诊断定位了两项问题：

- 工作台只改变 iframe 的 URL fragment 时，Chrome 原生 PDF 阅读器仍
  显示第 1 页。改为浅克隆并原位替换 iframe，保留名称、标题、样式及
  原有受保护版本 URL，使目标页真正重新加载。
- 跳页后截图仍失败：预览框顶/底为 593.67/1041.67 像素，高 448，
  外层视口仅高 857，截图只有约 263 像素高。通过 WebDriver 滚轮动作
  滚动外层页面后，两个浏览器场景、20 项断言通过。正式驱动仅在预览框
  越界时执行该动作，JS 只读取位置，不操作 PDF 内部内容。

保留原有颜色比例、正文区域指纹、精确版本、权限和分片 PUT 断言，未
放宽验收标准。新增截图越界回归先失败后通过，驱动自测 25/25；UI
契约 8/8、行为 18/18。定向截图已人工查看：分别包含 PAGE ONE 红色
页面、PAGE TWO 蓝色及黄色标记。定向结果不能替代修正后的完整阶段门。

第三轮在干净提交 `695f78cebfc257ca208f7a1271a6010005b66302` 上执行，
时间为 15:26:00–15:38:52（北京时间）。基础套件和 HTTP 再次全部通过，
正式浏览器驱动也取得 2/2 场景、20 项断言；随后四路流式测试因服务
进程 RSS 增长不严格小于 64 MiB 失败，流程在中断测试前退出。
采样中整个测试组内存最高约 1070 MiB，宿主机可用内存最低约 1762 MiB，
观察到的内存限额和 OOM 事件均为零；服务器未失去响应。

后续只复用二进制的分阶段测量定位到上传阶段：服务 RSS 从 9711616
升至 72622080 bytes，合并和下载没有继续增长。这次增长 62910464
bytes（约 60 MiB），虽恰好低于门槛，却不能推翻前轮失败。
根因是网络层 ET 读取循环先将 socket 可读数据全部累积，之后才调用
流式正文处理器；动态缓冲扩容使峰值随时序波动。

修正将每次成功读取限制为 64 KiB，先交给解析器消费，再通过既有
EPOLLONESHOT 重装处理剩余数据。发布业务结果前保留额外排队字节检查，
避免在读取窗口边界错误接受流水线请求。新增真实 socket/epoll 回归
在旧代码中失败，修正后聚焦连接测试 5/5；原四路、32 MiB、8 MiB 分片、
严格小于 64 MiB 的实测门槛不变。修正后的受限定向实测为 69 项断言
通过，基线 RSS 9404416 bytes、最终峰值 10907648 bytes，增长仅
1503232 bytes（约 1.43 MiB）；上传、合并、下载均验证四路重叠和最终
内容哈希。完整阶段门仍须以修正提交上的完整运行结果为准。

## 运行方式

使用一个独立的、由非 root 测试用户拥有的本地 Git clone，固定到实际
启动前选定的干净提交并记录完整 SHA；保留原工作树、现有 MySQL 和业务服务。浏览器与驱动放在这个
临时工作区内，用户 HOME 单独建目录并设为 0700。不要以 root 关闭浏览器
沙箱，也不要修改系统 swap 或其他服务配置。

以下是已准备的完整运行命令；执行前须取得本次风险授权，并将
`m1_run_root` 设置为已准备的独立临时工作区的绝对路径。
其中应已有 `repo/`、`home/`、`bin/node`（本机验证版本 24.18.0）、
`chrome-linux64/`、`chromedriver-linux64/`。
代码副本须为干净的目标提交，磁盘仍须是已验证的 `/dev/vda`。

```bash
: "${m1_run_root:?set the prepared isolated workspace first}"
systemd-run --unit=smartdocs-m1-acceptance --no-block \
  -p User=nobody -p Group=nogroup \
  -p WorkingDirectory="$m1_run_root/repo" \
  -p PrivateTmp=yes -p BindPaths="$m1_run_root" \
  -p MemoryHigh=1200M -p MemoryMax=1600M -p MemorySwapMax=0 \
  -p OOMPolicy=stop -p CPUQuota=100% -p TasksMax=256 -p Nice=10 \
  -p IOReadBandwidthMax='/dev/vda 5M' \
  -p IOWriteBandwidthMax='/dev/vda 5M' \
  -p IOReadIOPSMax='/dev/vda 200' -p IOWriteIOPSMax='/dev/vda 200' \
  -p RuntimeMaxSec=45min -p TimeoutStopSec=20s -p RemainAfterExit=yes \
  -p StandardOutput="append:$m1_run_root/acceptance.log" \
  -p StandardError=inherit \
  -p IOAccounting=yes -p CPUAccounting=yes -p MemoryAccounting=yes \
  --setenv="HOME=$m1_run_root/home" \
  --setenv="PATH=$m1_run_root/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  --setenv=SMARTDOCS_BUILD_JOBS=1 --setenv=PYTHONUNBUFFERED=1 \
  --setenv="SMARTDOCS_BROWSER_BIN=$m1_run_root/chrome-linux64/chrome" \
  --setenv="SMARTDOCS_WEBDRIVER_BIN=$m1_run_root/chromedriver-linux64/chromedriver" \
  /usr/bin/bash test/e2e/run_m1.sh
```

`PrivateTmp` 隔离验收的数据库、存储和浏览器 profile；`BindPaths` 让独立
代码副本和浏览器在这个临时命名空间里可见。只设置 `PrivateTmp` 而没有
绑定工作区，会导致 `200/CHDIR`，这一点已通过探针确认。

45 分钟是终止上限，不是预计耗时。该命令仍执行完整、顺序的构建和验收，
保持四路 32 MiB 流式测量、二十轮中断、真实 PDF 双页截图等原有门槛。
固定的 unit 名防止该入口同时启动第二个实例；运行期间禁止另启独立构建、
其他完整验收或压力测试。不要为绕过失败而删除测试或放宽正确性门槛。

## 观察、停止和结果处理

```bash
systemctl show smartdocs-m1-acceptance \
  -p ActiveState -p SubState -p Result -p ExecMainStatus \
  -p MemoryCurrent -p CPUUsageNSec -p TasksCurrent
cat /sys/fs/cgroup/system.slice/smartdocs-m1-acceptance.service/memory.events
free -m
vmstat 1 3
iostat -xz 1 3
tail -n 15 "$m1_run_root/acceptance.log"
```

内存硬上限针对整个测试组，内存超限时允许测试失败；不允许拖垮宿主机
来换取通过。I/O 限速会使测试进程等待，须结合设备延迟、内存、业务
响应和 cgroup 数据判断；不能仅凭 PSI 非零就判定机器再次故障。
若出现持续系统交互变慢、业务异常、可用内存跌到 512 MiB 以下或测试组
无法继续推进，应立即停止这一个测试组并保留日志，分析后再决定是否重试：

```bash
systemctl stop smartdocs-m1-acceptance
```

启动命令返回零只代表 systemd 接受了请求。必须确认主进程退出码为零、
`Result=success`、日志中有完整汇总，并检查生成的 schema-4 聚合结果、
schema-2 浏览器结果、两张截图、流式结果及提交/dirty 信息。原运行器
正常退出时会删除其内部临时目录；本方案保留外层日志和 clone 中的
`docs/evidence/m1/latest/`，不要假设失败时也能拿到完整内部诊断文件。
只有审阅通过后才能将证据复制到 `verified/` 并更新 M1 阶段状态。
