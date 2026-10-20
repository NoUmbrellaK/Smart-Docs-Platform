# M5：三项实际准入拒绝记录

2026-10-21，在当前ECS对冻结任务Q08、Q09、A09分别运行一次既有
[单案例CLI](../case-cli/README.md)的`--admission-only`模式。
被测代码`113f94e1453db92c0685d2777951b3e48308c7ee`，没有修改产品或测试实现。
每项重新创建独立MySQL schema16及人工资料，经真实C++ HTTP鉴权/任务创建入口拒绝。
这不是模型模拟响应，也不把旧前置预检重复算作本轮任务。

## 观测与统计边界

| 冻结案例 | 角色及场景 | 预期HTTP | 实际HTTP | 采集 | 业务评审 |
| --- | --- | ---: | ---: | --- | --- |
| Q08 | editor请求未批准的新版本 | 404 | 404 | 完整 | 未判定 |
| Q09 | editor请求仅内部管理资料用于AI | 404 | 404 | 完整 | 未判定 |
| A09 | reader请求归档 | 403 | 403 | 完整 | 未判定 |

三项CLI正常结束，`run_result=null`、`max_model_calls=0`，没有Agent审计文件、
远程模型请求、真实key读取、归档确认或执行。CLI必填的`--max-calls 1`不改变
准入拒绝分支实际零调用：该分支不会进入`run_context`，运行网络也仅允许localhost。
没有自动重跑；每次隔离库的10条迁移PASS另计，不是新增业务案例。

**这里只确认三项实际准入状态符合预期，不宣布3/30业务通过。**
空白评审表已由既有`benchmark_review.py prepare`排他生成，并通过`summarize`验证；
三项`business_verdict=null`、`execution_verified=false`，实际引用均0条，
引用准确率为null而非100%。原分母仍为30，另27项模型任务尚未执行。
本轮未进行人工语义评审，完整任务标准不能只靠HTTP状态自动判定。

覆盖细节：

- Q08固定装载旧/新版本，新版未获批准；没有继承旧版外发批准。
- Q09装载器另以管理员身份验证普通下载200及内容一致；本轮没有单独记录editor下载，
  因而不将这一前置检查扩大为该角色完整下载验收。
- A09创建归档任务返回403，角色任务列表为空；本轮不声称取得独立的失败后目录位置快照，
  也没有用模拟确认冒充真实用户确认。

## 证据与私有原始记录

[results.json](results.json)仅为白名单化公开摘要：实际状态、环境、代码版本、
时间、执行上限、空白评审统计及原始文件哈希，不含原始资料、会话或凭据。
[运行日志](journal.txt)保留所有三次摘要和正常退出记录。
[全30项状态台账](../fixed-task-results.json)保留所有未执行项，避免缩小分母。
冻结的`tasks.json`不修改，其`run_status=not_run`是冻结时的历史字段；
后续运行观测以该独立台账及所链证据为准，不回写输入或预期答案。

私有原件暂存`/tmp/smartdocs-m5-admission.t8qMSb/{Q08,Q09,A09}/`：
目录0700、文件0600、所有者nobody；包含`initial/snapshot.json`、`summary.json`、
`review.json`。没有提交原始快照或评审表。公开`private_artifacts.*.sha256`是
文件字节SHA-256；`review_summary.snapshot_sha256`是评审工具的规范化JSON摘要，
两者定义不同，不能直接比较。
临时目录不是持久备份，最终交付前仍需将原件归档到批准的受控持久目录。

复核现有原件只需离线命令（以原件所有者运行，不重新执行模型或测试）：

```sh
python3 -B test/e2e/benchmark_review.py summarize \
  /tmp/smartdocs-m5-admission.t8qMSb/Q08/initial/snapshot.json \
  /tmp/smartdocs-m5-admission.t8qMSb/Q08/review.json
```

Q09/A09替换案例ID。输入/允许答案仍在冻结
[tasks.json](../../../../test/fixtures/agent-p0/tasks.json)，
其SHA-256及corpus SHA-256绑定于公开结果。实际CLI参数见日志；运行封装与依赖见
[单案例CLI说明](../case-cli/README.md)，不得将此入口直接指向业务库。

## 环境、资源与清理

CPU Xeon Platinum 2 vCPU、3495MiB RAM、Python3.10.12、MySQL8.0.46；
本地关键词解析，无向量服务或模型。实际
[Q08配置](running-q08.txt)及[Q09/A09配置](running-q09-a09.txt)记录：
CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、TasksMax256、
磁盘读写各5MB/s与200IOPS；nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
仅localhost网络，低于512MiB可用内存时停止自有测试单元，不放宽限额。

Q08于00:20:48启动、00:22:35退出；Q09/A09串行单元于00:23:36启动、
00:27:12退出（Asia/Shanghai，含建库/迁移/清理）。
后两项使用`set -eu`串行循环，每项独立启动和清理临时库，失败不会自动重跑。
日志的初始化空密码警告来自禁止TCP网络的临时MySQL，不是业务库账户修改。

[终态](final-status.txt)确认两个单元均inactive/dead、MainPID0、cgroup移除，
slice TasksCurrent0，无nobody进程残留，原MySQL仍active。
memory.high累计80521未增加，max/oom/oom_kill均0，可用内存2052MiB；
这是离散观测，不是峰值RSS或长期稳定性证明。
包装器仅清理自身临时数据库/存储，原始结果文件保留，没有删除业务资料或改生产服务。

沿用并复核[28项源文件及两个二进制指纹](../case-cli/sources.sha256)，
工作树与实际受限副本逐字节匹配，未编译。本次只新增结果证据与文档。
此前三次DeepSeek烟测授权已消费，本轮没有新增付费调用或授权；
27项模型任务、全部业务评审、实际引用检查、匹配版本部署验证及完整演示仍待完成。
M5及P0尚未验收。

归档一致性复核：28个源文件及两个二进制哈希匹配；冻结任务ID/类别与台账一一对应，
三类各10项、3项准入观测/27项未执行、所有业务判定及完成率为空；
30条迁移PASS未混入任务统计。259个本地链接目标存在，私有快照与空白评审表仍可通过
离线校验，凭据模式扫描无匹配，`git diff --check`通过。
这些检查只验证证据一致性，不是重新运行产品测试或人工业务判定。
独立只读审查未发现阻断项；审查者受目录权限限制，未独立复算私有原件哈希，
因此其结论只覆盖公开摘要、日志、台账和冻结输入，私有原件验证由本轮主执行者完成。
