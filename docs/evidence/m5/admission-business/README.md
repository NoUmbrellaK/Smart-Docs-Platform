# 冻结准入案例的业务条件补验

2026-10-21，基线 `d50fb3c` 加[补验入口](../../../../test/e2e/m5_admission_business_test.py)。
保留[首次准入观测](../admission-results/README.md)，本轮补其明确缺失的角色下载、
失败后位置和原文一致性证据。未修改冻结题目、允许答案、产品代码或原始评审表。

## 结果

[完整日志](verified.txt)与[结构化观测](results.json)：单次 **3/3 用例通过**，
0失败/错误/跳过，测试本体4.351秒。01:52:40—01:54:28（Asia/Shanghai）
含隔离库初始化、schema16迁移和清理；十条迁移PASS另计。

| 案例 | 冻结角色 | 实际观测 |
| --- | --- | --- |
| Q08 | editor | 新版本普通下载200且SHA匹配；AI读取404、任务创建404；旧批准未使新版获准 |
| Q09 | editor | 内部资料由该editor实际下载200且SHA匹配；AI读取404、任务创建404 |
| A09 | reader | 归档任务创建403；文件ID、目录、当前版本和普通下载内容均保持不变 |

每例先复用既有 `prepare_case` 装载并进行一次拒绝预检，再显式进行一次实测创建请求。
新请求前后均实际GET文件列表和下载原文；首次预检后的文件ID/目录/版本也必须与
冻结夹具的初始映射一致，避免第一次拒绝发生错误移动后，第二次比较仍假通过。
原文SHA按冻结文本核对，不能仅凭版本ID相同推断未覆盖。

每例两次创建请求，共六次拒绝，不是六项任务；与旧记录分开，固定分母仍为30。
六表 `agent_tasks`、`agent_runs`、`agent_tool_calls`、`archive_plans`、
`archive_decisions`、`handover_reports` 均为零（归档执行结果存于 archive_decisions）。
没有模型执行、归档确认或归档执行。

该补验直接覆盖三项冻结拒绝案例的客观条件；`contract_checks=pass` 是自动断言结果。
**未填写真人业务评审，不宣布3/30真人评审通过或30项整体完成率。**
实际引用0条，引用准确率为空，不是100%。其余27项模型任务仍未执行。
没有用模型替身替代这些拒绝结果，也不将零模型调用解释为模型语义能力。

## 环境、复现与边界

[预检查指纹和共享资源限额](preflight.txt)、[实际运行属性和最终脚本指纹](running.txt)。
preflight中的新增脚本指纹是审查前版本；独立复核指出首次拒绝后的基线比较缺口后，
增加上述初始映射断言，**实际被测版本以running/final-status的SHA为准**。
该修正发生在首次实测前，不存在被隐去的失败运行。其余相关源码、冻结数据、
迁移和二进制与受限副本逐字节核对；未重新编译，不声称完整checkout同步。

在安全副本 `/tmp/smartdocs-safe.h4SzvX/repo` 运行：

```sh
systemd-run --unit=smartdocs-m5-admission-business \
  --property=User=nobody --property=Group=nogroup \
  --property=Slice=smartdocsvectorcheck.slice \
  --property=WorkingDirectory=/tmp/smartdocs-safe.h4SzvX/repo \
  --property=Nice=10 --property=UMask=0077 --property=NoNewPrivileges=yes \
  --property=KillMode=control-group --property=TimeoutStopSec=5 \
  --property=RuntimeMaxSec=480 \
  --property=IPAddressDeny=any --property=IPAddressAllow=localhost \
  --setenv=PYTHONPATH=python --setenv=PYTHONDONTWRITEBYTECODE=1 \
  /bin/bash scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m5_admission_business_test.py
```

三个用例串行共用一个临时MySQL，各自创建独立项目/角色；没有连接业务库。
实际MySQL8.0.46，schema16，禁TCP；初始化空密码警告只属于隔离库。
沿用CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、
磁盘读写各5MB/s和200IOPS，不提高限额。没有读取API key，单元仅允许环回网络。
公开结果只含合成对象ID、角色、状态、SHA和计数，不含下载正文或会话。

[终态](final-status.txt)为success/exit0、inactive/dead、MainPID0、slice TasksCurrent0，
无nobody残留进程，原MySQL active；夹具清理自身临时数据库和存储。
memory.high保持82471，max/oom/oom_kill均0，结束观测可用内存2048MiB。
不是峰值RSS、性能或长期稳定性测量。

独立复核已确认初始状态比较缺口关闭，无剩余重要项。
本轮不包括真实模型业务评测、引用语义人工检查、生产部署或完整演示；M5/P0仍未完成。
