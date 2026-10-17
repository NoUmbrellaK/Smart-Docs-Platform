# M2 常驻编码监督层与私有协议验证

2026-10-18；基线 `02465d57c97cb27c91a60e6700ed62eb9f630e17`。
本轮新增可选 `smartdocs.resident_service`，把已验证的常驻核心接入现有私有 Unix
协议；未自动部署、未修改旧逐请求入口、客户端、C++ 权限或数据库。
设计和验证步骤见 [计划](../../../superpowers/plans/2026-10-18-resident-service.md)，
人工部署与退出顺序见 [手册](../../../deployment/m2-vector-worker.md#可选常驻服务需单独验收不默认启用)。

## 实际边界

- 轻量父进程持原模型 flock；一个子进程持固定 SHA 校验后的模型。IPC 是有界
  四字节长度前缀（先验长度再分配）；用户协议仍是输入半关闭、输出 EOF。
- 模型加载后通知父进程，父进程才监听并发出 systemd READY。启动最多 180 秒，
  独立于每请求 60 秒总预算；启动失败不开放 socket。非 root、内核禁 IP socket。
- 单请求总预算包含接收/IPC/发送；忙碌连接最多用 1 秒且不超过当前 deadline
  收取输入再完整回复 busy。慢/超大竞争连接关闭，不承诺收到错误体，无无限队列。
- 超时/崩溃/非法 IPC 使实例终止，不重放请求。先 SIGKILL 模型进程组，再最多
  wait 5 秒；SIGTERM 同样清理。若内核 D 状态阻止退出则服务失败，必须确认旧
  cgroup 全空后人工重启，不声称应用能保证不可中断 IO 立即退出。
- 父进程 512 MiB、子进程 2 GiB 虚拟内存上限；子进程启动 memfd 文件限 256 MiB，
  加载后 2 MiB，core dump=0。复用进程不用累计 60 秒 CPU 寿命限制；原 CPUQuota
  与父进程每请求墙钟 watchdog 保持。子进程环境严格白名单，无业务/API 凭据。
- socket 父目录 0700、文件 0600、同账号；现存路径拒绝，不自动删除未知对象。
  清理只删除本实例创建且 inode/device 仍匹配的路径。生产 root 只读模型要求不变。

## 先失败再修正

1. 新增 6 项真实进程/Unix 协议测试，缺监督模块时全部失败，见
   [initial-red.txt](initial-red.txt)。测试只替换模型构造/计算为人工向量，监督、
   子进程、IPC、锁和终止都真实；不是模型质量或业务权限证据。
2. 工具沙箱试跑无法创建 Unix socket（PermissionError），未当作业务失败；转到
   非 root 受限单元后，发现 busy 回复因关闭未读连接而被 ECONNRESET 掩盖。
3. 初修仅 drain 已有数据，独立审查指出 connect 先于 send 的竞争。新增延迟
   150 ms 发送的确定性用例，在旧实现重现 BrokenPipe，见
   [busy-race-red.txt](busy-race-red.txt)。最终按半关闭协议有界收取并完整发送。
4. 审查还要求限制回收等待：加 5 秒上限，并避免异常时二次等待；忙碌处理后
   重新核对总 deadline，不因竞争连接延长预算。

## 验证环境和范围

当前 ECS 原共享 smartdocsvectorcheck.slice：MemoryHigh=1200 MiB、Max=1600 MiB、
Swap=0、CPUQuota=100%、Tasks=256、读写各 5 MB/s 和 200 IOPS、Nice=10。
重型测试串行。Python 回归单元总寿命 120 秒；真实模型服务单元总寿命 300 秒，
nobody/nogroup、PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、NoNewPrivileges。
没有提高主机配额、清空页缓存、迁移业务库或调用 DeepSeek。

真实测试使用旧 `request_encoding`，六个旧基线样例的完整输出固定指纹
`6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`；不是生成新期望值。
初验 **1/1、6 项检查通过**，见 [real-service-initial.txt](real-service-initial.txt)：
同一子进程三次请求 0.605/0.643/0.571 秒、启动 3.128 秒、模型峰值 640804 KiB。
之前模型测试已读文件，不称为冷启动性能；不是整个 cgroup 峰值，也不是浏览器
组合负载结果。最终复验另加入非法输入后仍可在同子进程处理合法请求。

复现命令须置于上述受限单元：

```bash
PYTHONPATH=python /path/to/parser-python -B -m unittest discover -s test/python -v
PYTHONPATH=python /path/to/parser-python -B test/e2e/m2_resident_service_test.py /path/to/model /path/to/model-python
```

当前没有浏览器/C++/数据库组合复验，不以协议和模型单独通过替代 M2 验收；下一步
在新鲜常驻服务 READY 后，用原 hybrid_browser_test.py、原共享配额完成该组合。
不改来源/新版/批准撤销断言，不追加自动重试。M3–M5 尚未开始。

## 最终结果与清理

- 最终完整 Python **95/95、20.680 秒**，含 9 项监督层测试，见
  [python-regression.txt](python-regression.txt)。覆盖 READY 通知、启动失败/超时、
  SIGSTOP 模拟原生卡死后的超时杀回收、busy 延迟发送、慢客户端、输入/输出限额、
  子进程崩溃、共享锁、拒绝非受控网络及现存路径、SIGTERM 回收。
- 最终真实模型 **1/1、6 项检查通过**，见 [real-service-final.txt](real-service-final.txt)。
  初始化 3.322 秒；三次计时调用 0.787/0.623/0.574 秒，另有一次非法输入后的合法
  请求用于证明仍是同一子进程。模型峰值 639224 KiB（约 624 MiB），完整旧指纹一致。
  同样不是冷启动或整个 cgroup 峰值，不累计初验/复验成更大业务分母。
- 独立只读审查：两项 Important 已修复并复核，无剩余 Critical/Important。
  systemd 与应用均为 5 秒停止/回收等待的清理接管限制已补到部署手册。
- 本轮 memory.high 保持 64879（增量 0）；max/oom/oom_kill 均 0。
  最终测试单元退出码 0、inactive/dead，所有 smartdocs 运行单元为 0；原 MySQL
  active。详细终态见 [unit-status.txt](unit-status.txt)。测试临时目录由夹具清理，
  未删除原模型或业务数据。没有人为制造内核 D 状态，不能声称该异常已动态验证。
- [源码指纹](source-sha256.txt)及逐文件 cmp 确认最终工作树与执行副本一致。
  未改动的 C++/前端全量套件未重跑。
