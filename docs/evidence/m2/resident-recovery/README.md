# M2 常驻编码中断与显式重试联合验证

2026-10-18，北京时间；实现基线 `272ecd5de56cf4a3e035f503eef9b4080ca77cde`。
本增量新增真实故障测试，不修改生产实现，不调用 DeepSeek，不运行浏览器。
M2 仍未验收。

## 实际结果

[完整日志](journal.txt)：**1/1 通过**，测试本体 103.306 秒（包含等待操作者
重启编码服务的时间，不是编码性能）；schema 5 与 103 文件迁移检查另计。

- 02:58:04 第二批请求已接收、模型已暂停；暂存 **1 批 / 8 片段 / 16 窗口**，
  业务只返回 keyword_only，partial_vectors_visible=false。
- 02:58:05 监督层及模型已退出，任务 failed、attempt_count=1，真实错误为
  `embedding_invalid_result`（停止导致未收到完整响应）；不包装成成功或自动重试。
  此时已经重启过实际 C++ 并验证相同失败记录与暂存数据仍在。
- [恢复前状态](before-restart.txt)确认旧服务 MainPID=0、inactive/dead、无 cgroup；
  启动命令额外以 `&&` 联结旧 cgroup、PID、socket 不存在检查，成功后才启动新单元。
- 02:59:41 新编码服务 READY 后业务仍须显式 retry；新 attempt 清空旧暂存，
  两批重新编码为 **2 批 / 9 片段 / 17 窗口**，attempt_count=2，固定版本未变。
  后端实际返回 hybrid、9 个唯一 chunk，全部带 vector 匹配来源。
- 最终只读审查：信号校验 Important 和 fd 清理 Minor 均在执行前修复并复核，
  无剩余 Critical/Important。没有为通过测试修改生产状态机、自动重试或资源限制。

[资源采样](resource-samples.txt)中 memory.high 从 77682 增至 80269，
max/oom/oom_kill 为 0。存在回收和 IO 等待，不宣称没有压力；离散采样不是峰值。
[终态](final-status.txt)：三个单元 exit 0、MainPID=0、inactive/dead，smartdocs
运行单元为 0，原 MySQL active，主机 MemAvailable 1845 MiB。
测试隔离库和存储已由夹具清理，原模型与业务数据保留。

[源码及实际二进制 SHA](source-sha256.txt)与执行副本核对一致；C++/头文件、
Makefile、resources、Python 实现及既有测试逐文件 cmp，执行副本
`make -q bin/server bin/smartdocs-admin` exit 0。没有重编译，未使用工作树旧 bin。
本轮只跑此新增联合场景，不把历史全量 C++/Python/浏览器数量算作本轮回归。

## 场景与断言

`test/e2e/m2_resident_recovery_test.py` 复用既有真实上传、worker、C++/MySQL 和
私有 Unix 编码客户端。人工资料 `b"backup " * 1372` 为 9604 字节，9 个片段，
需要 8+1 两批；先进行真实解析并批准具体版本，编码查询 `backup` 一次。
该查询向量仅在同一测试内复用，以隔离数据库发布门槛；不是新查询编码性能证据。

故障注入位置是首批已入库、第二批已被监督层接受但模型进程暂停时：

1. 第一批使用真实模型，第二次 encoder 调用前对真实模型子进程发送 SIGSTOP。
2. 无效输入探针只能返回 invalid_input 或 busy；busy 证明另一个合法第二批请求
   已被监督层接受。此时第一批 8 个片段已持久写入，但文档仍 processing，
   带真实查询向量的业务检索只能 keyword_only，不能暴露部分向量。
3. 向专用测试监督进程发送 SIGTERM；检查父子均退出、socket 消失，worker
   不重放编码，C++ 记录 failed 与安全错误类型，关键词仍可用。
4. 重启实际 C++，检查失败状态、attempt_count 与部分暂存记录仍在，worker idle。
5. 外部操作者确认旧模型服务 cgroup 已空后手工启动新实例；服务恢复后任务
   仍 failed、worker 仍 idle。仅调用真实 `/retry` 才进入 pending。
6. 新 attempt 开始清除旧部分记录，从第 0 批重新真实编码；最后要求两批、
   9 个唯一片段、attempt_count=2、三项状态 completed，hybrid 实际包含向量命中。

“无部分发布”指未完成向量不参与业务检索，不是数据库从未写入暂存行。
失败后暂存行保留，新 attempt claim 时清理；不伪称失败即物理删除所有记录。
这是可控 SIGSTOP+SIGTERM 故障注入，不模拟内核 D 状态，也不代表所有宕机窗口。

## 安全边界与复现

信号只允许作用于同账号、精确 cgroup 路径
`/smartdocsvectorcheck.slice/smartdocs-m2-recovery-encoder.service` 的父进程和
唯一直接子进程；分别打开 pidfd 后再次核对 cgroup，避免相似服务名或 PID 复用。
静态审查发现初稿子串校验过宽，运行前已修正为完整匹配并立即登记 fd 清理。
测试代码不自动重启服务；外部操作者仅在旧实例已终止后启动替代实例。

原共享切片：MemoryHigh=1200 MiB、MemoryMax=1600 MiB、Swap=0、CPUQuota=100%、
Tasks=256，/dev/vda 读写各 5 MB/s、200 IOPS。nobody/nogroup、Nice=10、UMask=0077、
NoNewPrivileges=yes、KillMode=control-group、TimeoutStopSec=5。编码单元禁 IP socket
（PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX），Type=notify、启动190秒、
RuntimeMaxSec=900；业务单元 RuntimeMaxSec=780。原业务数据库不参与测试。
不提高配额或清页缓存；观察到主机 MemAvailable <512 MiB 即停止。

执行副本 `/tmp/smartdocs-safe.h4SzvX/repo`，模型沿用原共享 model.lock。
先在受限编码单元运行并等到 READY：

```bash
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m smartdocs.resident_service \
  /tmp/smartdocs-safe.h4SzvX/recovery-socket/encoder.sock \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/model \
  /tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/vector-socket/model.lock
```

业务单元设置 PYTHONPATH=python、SMARTDOCS_ENCODER_SOCKET 为上述 socket，
SMARTDOCS_TEST_ENCODER_PID 必须取刚刚 READY 的专用单元 MainPID，不能复用日志旧值：

```bash
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_resident_recovery_test.py
```

见到 `await_manual_encoder_restart` 后检查旧单元 MainPID=0、cgroup 不存在或
无进程，再以相同参数启动 `smartdocs-m2-recovery-encoder-restarted.service`。
测试最多等待240秒，恢复后不自动批准或重试业务任务。结束时显式停止新编码
单元，并检查全部测试进程退出、原 MySQL 正常。没有凭据或文档正文日志。
