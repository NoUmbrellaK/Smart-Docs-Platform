# A27：成对恢复后的真实本地向量重建

2026-10-19，基线 `7c8f947cb83fe57703bd9d646a959b76289e9eca` 加本提交。
按最小改动原则，给 [A26 同一恢复脚本](../../../../test/e2e/m3_backup_restore_test.py)
增加显式 `--vectors` 模式，不改产品接口、存储、数据库 schema 或模型。
保留 A26 的完整文件/版本/角色/私有报告/固定引用/关键词/续传及原库不变检查，
在同一次备份恢复中加入真实本地编码、向量恢复和重新生成，而非拼接不同运行的成功步骤。

## 新增检查

1. 备份前，经现有 vector_worker/C++ 队列对两份当前获批文本各编码一批，
   旧版本与未获批新版不进入编码。通过真实查询协调器编码查询并获得 hybrid 结果，
   两份资料的 match_sources 均包含 vector。
2. 向量窗口和批次回执随整个数据库导出/导入；新库新目录的新 C++ 服务读取恢复数据。
   在原有整库行快照核对之外，比较含 chunk、窗口、job、修订和全部384维坐标的指纹，
   恢复后的混合候选及覆盖字段与备份前一致（按固定 chunk 排序，不承诺相关度排名）。
3. 在恢复环境通过原有管理员批准接口，对相同两个获批版本显式重排队。
   两个任务不再 completed 时，验证 keyword_only、vector_index_unavailable、
   vector_completed_files=0，仍命中原来的两个固定片段，不把空结果误判为降级成功。
4. worker 领取新尝试后，编码回调先只读核对该片段的旧向量已删除，再进行真实推理。
   两个新尝试完成后计数合计增加2，完整向量行指纹与原值一致，混合结果重新可用；
   报告仍可读、引用不变，之后原有撤销来源、续传和原库隔离检查继续执行。

调用计数由实际编码入口累计：4次文档批次（原库2、恢复重建2）与4次查询编码。
都是已有缓存 MiniLM 的本地推理；没有 DeepSeek、真实 key、云端模型或付费 token。
报告内容仍是人工构造的测试报告，不作为模型语义效果评测。

## 实际验证

- [首次运行](first-run.txt)：1/1失败，36.483秒。报告出处接口包含 vector_state，
  原快照在初始向量生成前记录 pending，恢复后返回 completed；文件/内容未变化。
  修正为所有准备工作完成后再读取备份前快照，没有修改或放宽产品行为。
- [完整向量复验](verified.txt)：**1/1通过，36.758秒，失败/跳过0**。
  同一场景包含原有9个存储文件、4个版本、1条报告引用及1次续传核对，
  实际本地编码请求8次；向量重新生成、关键词降级和固定引用检查均执行。
  关键词重建后的排名变化仍如实记录，不承诺排名固定。
- 只读审查发现空候选可能通过降级检查，已补齐两个固定 chunk 内容完全相等、
  vector_completed_files=0 的断言，并包含在上述真实复验中。
- 编码器停止后，[默认模式回归](default-regression.txt) **1/1通过，21.940秒**，
  未配置编码 socket，本地编码请求0。它是原有模式回归，不与向量模式拼接为两个不同业务资料集。

[环境和终态](environment.txt)保存实际限额、依赖、运行中身份、源文件及二进制哈希。
向量复验北京时间23:05:19–23:07:49，默认回归23:08:30–23:10:37，均含隔离数据库准备/清理；
测试本体耗时不是生产恢复时间或冷启动性能。编码器23:01:16实际READY，23:08:27正常停止；
三次数据库测试串行。全部单元MainPID=0、slice TasksCurrent=0，原MySQL仍active；
memory.high保持80521，max/oom/oom_kill=0，无新增high/OOM。临时库、备份、storage、
socket和本轮空socket目录已清理，原模型缓存保留。没有产品修改、构建或真实API key读取。

## 复现

沿用 A26 的临时 MySQL、环回服务、nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges、KillMode=control-group、TimeoutStopSec5；业务单元 RuntimeMaxSec780、
仅允许 localhost。编码服务 Type=notify，NotifyAccess=main、TimeoutStartSec190、
RuntimeMaxSec900、PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX；实际 READY 才启动业务测试。
两个服务共用原 `smartdocsvectorcheck.slice`，不是各自分配一份内存预算：
CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、读写各5MB/s及200IOPS。

```sh
# 在上述受限单元与可由 nobody 读取的测试副本中，不是无资源限制执行建议。
PYTHONPATH=python:test/e2e \
SMARTDOCS_TEST_PARSER_PYTHON=/path/to/parser-venv/bin/python \
SMARTDOCS_ENCODER_SOCKET=/private/test-directory/encoder.sock \
scripts/test-with-mysql.sh -- /path/to/mcp-venv/bin/python -B test/e2e/m3_backup_restore_test.py --vectors
```

编码器使用既有 `smartdocs.resident_service`、已缓存模型及固定依赖；不重新下载。
完整运维步骤见[部署手册](../../../deployment/m1-single-host.md#向量恢复与重新生成)。

## 边界

这是本机 schema16/固定模型下人工小资料的恢复验证，不是生产灾备、异机/跨版本迁移、
模型缓存丢失恢复、长时稳定性、RPO/RTO 或性能结果。词向量逐位相等仅为此次固定环境实测，
不是任意 CPU/依赖组合的数值一致性承诺。已有编码器在恢复期间保持运行，业务 C++ 是新进程。
未执行30项真实模型业务任务或50条语义引用评测，M3和整个P0仍未验收；不据此跳到M5。
