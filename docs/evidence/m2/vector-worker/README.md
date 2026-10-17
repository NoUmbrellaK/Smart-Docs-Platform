# M2 Python 向量调度闭环

日期 2026-10-17，基线 `b935133942d9b8a8ca8e0371fade71bc8827383d`。
本增量把已存在的 C++ 向量队列接至有界本地编码器，不包含混合检索。
被测改动源码见 [source-sha256.txt](source-sha256.txt)，部署边界见
[向量 worker 手册](../../../deployment/m2-vector-worker.md)。

## 已验证范围

- 完整 Python 回归 **59/59**，包含新增 12 项调度/Unix 流协议/安全回执测试。
  本机 HTTP 夹具验证分批顺序、发布响应丢失后相同请求重放、未知提交结果退出、
  非法领取/批次/向量、socket 权限、输入输出上限、安全错误码及未禁网启动拒绝。
- 真实 C++ HTTP、临时 MySQL、固定真实模型闭环 **3/3**，耗时 **52.880 秒**
  （不含初始化数据库和后续 C++ 回归）：
  1. 单实例锁先于恢复、running 变 interrupted、不自动 retry；编码服务不存在
     时明确失败，关键词状态保留，人工 retry 后真实编码成功。
  2. 上传人工中文/emoji 文本，解析为 9 块；未批准时领取为空，批准后 CLI 经
     Unix 编码服务完成两批。SQL 验证 embedding_batches 为 2、持久化不同片段为 9，
     窗口数至少为 9；解析/关键词/向量状态全部 completed。
  3. 真实编码返回后撤销批准，旧结果被拒绝；任务 cancelled、版本向量状态
     not_applicable，数据库无批次/向量行。重新批准后新的尝试成功。
- schema 5 创建及 103 文件升级/重放夹具通过，没有把迁移本身当作已生成向量。
- 第三轮 C++ 全量输出 **135 passed / 0 failed / 0 skipped**，
  [完整原始输出](full-check.txt)同时包含 Python 和真实模型用例。
  但该组合单元最终为 timeout，不能称整条命令成功，时间边界见下。
- 只读审阅发现异常发布回执被当作失败后继续运行的问题；复现后改为立即退出，
  对失败回执也加同类校验。两个新断言均先观察到失败，再实现修复；复核未发现
  新的 Important/Critical。此复核不是独立的完整安全审计。

## 真实失败与资源调整

初始新增入口不存在，9 项测试失败；实现后本机沙箱禁止绑定 socket，转入受限
非 root 单元后 Python 56 项通过，再补禁网入口及不确定回执用例至 59 项。

`smartdocs-vector-pipeline-test` 首轮真实闭环 0/3：测试夹具误用 POST 调用既有
PUT 批准接口，全部为 405，尚未调用模型；修正测试方法，没有改动业务接口。

`smartdocs-vector-pipeline-test-2` 为 **2/3**：首个模型请求失败，直接查询隔离库
证实 last_error_code=embedding_timeout，后两例通过。[失败原始日志](initial-model-run.txt)保留。
当时临时 MySQL RSS 采样 440924 KiB，整个测试组触发 memory.high 回收限流，
没有 OOM。不能把首次超时从记录中删掉、也不能把第二轮称为全通过。

第三轮只调整临时数据库：performance_schema=OFF、InnoDB buffer pool=32 MiB。
运行时 SELECT 核实为 `0 / 33554432`，MySQL RSS 采样降为 172052 KiB。
没有增加模型 60 秒超时、组内存/CPU/I/O 上限，没有修改生产 MySQL。
第三轮通过全部三个真实模型用例；可能已有缓存预热，不能据此保证任意冷启动
均低于 60 秒，也不是搜索质量或吞吐量测量。

## 执行环境与复现

当前 ECS，Python 3.10；执行副本 `/tmp/smartdocs-safe.h4SzvX/repo`，不是干净构建。
模型目录 `/tmp/smartdocs-safe.h4SzvX/vector-probe/model`；编码解释器
`/tmp/smartdocs-safe.h4SzvX/vector-probe/venv/bin/python`。固定模型哈希、依赖和
修订继续使用 [有界编码器记录](../bounded-embeddings/README.md)。未重新下载模型。

先在非 root 单元串行构建 `make -j1 bin/server bin/smartdocs-admin`（成功）；
C++ 源码与基线相同，Python、脚本及新增测试与执行副本逐字节核对。

真实编码监听单元 `smartdocs-vector-encoder-test` 使用 systemd-socket-activate
的 `--accept --inetd`，不是直接安装生产 socket/service 模板。监听路径为
`/tmp/smartdocs-safe.h4SzvX/vector-socket/encoder.sock`，用户 nobody、目录 0700、
socket 0600；模型锁同目录。PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、
NoNewPrivileges=yes 已从运行单元核实。服务入口在 IP socket 可创建时拒绝运行，
实际成功编码继承该限制；模型子进程的环境仍为现有白名单。

编码服务与验证单元共用 `smartdocsvectorcheck.slice`：MemoryHigh=1200M、
MemoryMax=1600M、MemorySwapMax=0、CPUQuota=100%、TasksMax=256、磁盘读写各
5M 和 200 IOPS。所有模型批次串行；未让两个完整资源组同时消耗各自 1600 MiB。
第三轮开始前累计 memory.high 事件 2725，模型结束后 2761；max/oom/oom_kill
均为 0。采样不是峰值 RSS，不能宣传零限流。模型三例完成后，确认只剩监听进程
再停止临时编码单元；其结果 success/inactive。

全量组合单元 `smartdocs-vector-pipeline-test-3`，User=nobody/Group=nogroup，
PrivateTmp 并绑定受控副本，RuntimeMaxSec=900。核心命令：

```bash
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
PYTHONPATH=python SMARTDOCS_ENCODER_SOCKET=/tmp/smartdocs-safe.h4SzvX/vector-socket/encoder.sock \
  scripts/test-with-mysql.sh -- /bin/bash -c \
  '/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m2_vector_worker_test.py && stdbuf -oL ./bin/smartdocs_tests'
```

第三轮 20:17:48 启动，日志最后写入 20:32:48.187796262（含 RESULT 135），
20:32:49 触发 900 秒总时限，最终 Result=timeout / ExecMainStatus=15。
因此测试断言全通过，但组合命令未成功返回；退出/临时数据库清理未得到正常完成
确认。随后核实 MainPID=0、ControlGroup 为空，无运行中的 smartdocs 测试单元，
仅原生产 MySQL 仍在运行。不能把该单元表述为 success。
这种限 I/O 的全量组合复现需为收尾留时间，建议总墙钟上限 1800 秒，仍保持相同
内存/CPU/I/O 限额和每次模型 60 秒上限；不把延长总测试预算等同于性能改善。

随后使用相同源码、同一资源组独立复验本轮改动，不再串接未改动的 C++ 全量：
`smartdocs-vector-pipeline-final` 最终 **Result=success / ExecMainStatus=0 /
ActiveState=inactive**。[完整日志](final-check.txt)为 Python **59/59**，迁移通过，
真实模型 **3/3**（29.362 秒），包括临时数据库正常清理。此为重复验证，不能与
第三轮累加成 118 个 Python 或 6 个不同业务用例。监听使用同目录 `final.sock`，
相同禁网配置，验证结束后主动停止 `smartdocs-vector-encoder-final`，结果 success。
没有运行中的 smartdocs 测试单元，生产 mysql.service 仍 active。
成功的 transient unit 随后已被回收；另以 [journal 终态记录](unit-status.txt)
中的 `Deactivated successfully` 核实，不只依赖已回收单元的默认属性。

没有读取 DeepSeek key、付费推理、迁移业务库、安装生产常驻服务或重跑浏览器。
普通搜索仍为元数据/关键词检索；向量检索、融合和整个 M2 验收未完成。
