# 当前文件 HTTP 与20轮中断复验

2026-10-20，源码基线`771c231`。针对[验收索引](../acceptance-index.md#版本绑定与发布复验)
中M1后端后续变更，复用既有9项HTTP场景和6类20轮中断断言。
不是原`run_m1.sh`整套验收：本轮不运行浏览器、吞吐、UI或全套C++，不导入旧成绩伪称新运行。

## 构建与执行边界

[源码指纹](source-sha256.txt)绑定124个与受限副本逐字节一致的相关文件：
C++、迁移、Makefile、scripts、三个M1测试/辅助模块、迁移测试与M1夹具。
普通沙箱无权读取部分nobody私有文件，因此只读提权核对，未修改目录权限。

原故障注入二进制停留在10月17日，先通过[单线程增量构建](build-journal.txt)更新，
命令`make -j1 bin/server bin/smartdocs-admin bin/smartdocs_test_server`。
normal server/admin复用既有最新构建；故障版使用既有
`SMARTDOCS_ENABLE_FAULT_INJECTION=1`构建目标，不修改产品源码或上线二进制。
不是`make clean`后的完整重建。

[调用入口](invocation.py)只负责临时存储/环境、调用现有HTTP脚本及
`m1_interrupt_test.run_round`、输出结构化结果。
完整复用原`FAULT_ROUNDS`分布4/4/4/3/3/2和逐轮断言，不删减错误路径。
没有隔离MySQL socket时立即拒绝，已验证该拒绝路径。
正常HTTP使用`bin/server`；中断轮使用当前`bin/smartdocs_test_server`。
不调用原完整汇总入口，所以不输出其schema4“全套M1通过”结果，也不伪造其浏览器/吞吐前置记录。

## 资源与复现

当前ECS、目录`/tmp/smartdocs-safe.h4SzvX/repo`；[构建运行配置](build-running.txt)：
smartdocsvectorcheck.slice，CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
TasksMax256、/dev/vda读写各5MB/s及200IOPS；nobody/nogroup、Nice10、
UMask0077、NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
构建禁止全部IP流量，测试仅允许localhost。可用内存低于512MiB即停止本轮单元。
构建完全结束后才启动测试；不调大限额，不并行重型任务。

将与归档字节一致的invocation.py放在`/tmp/smartdocs-m5-file-invocation.py`，
在相同资源属性的测试单元中执行：

```sh
bash scripts/test-with-mysql.sh -- /usr/bin/python3 -B /tmp/smartdocs-m5-file-invocation.py
```

包装器创建禁用网络的独立MySQL，迁移到schema16后执行调用入口，正常退出清理隔离库。
入口另用TemporaryDirectory创建存储和日志，结束清理；只保留结构化证据，不提交cookie、
登录会话或原始服务器日志。使用人工测试账号/固定测试口令，不读取任何真实API key。
上传上限1MiB、分片16字节，线程/连接池各4，环回网络；不当作1GiB或公网性能测量。

## 实际结果

[原始测试日志](test-journal.txt)及从其JSON行提取的[结构化结果](results.json)：

- 正常版HTTP：**9/9场景、44断言、0失败**。
- 当前故障版中断：**20/20轮、305断言、0失败**；
  六类分布4/4/4/3/3/2，每轮退出86、重启就绪、最终SHA一致、
  duplicate_version_count=0及half_published_download=false。
- 包装器另有10条迁移PASS，不计入9项HTTP或20轮中断分母；
  不把历史20轮与本轮相加后声称固定测试规模是40轮。

构建23:34:36—23:37:55，测试23:38:44—23:40:53（Asia/Shanghai），
后者含临时建库/迁移/清理，不是纯上传性能耗时。
[实际测试配置](test-running.txt)与[终态](final-status.txt)确认两单元success/exit0、
inactive/dead，cgroup已移除，无nobody测试服务器/数据库残留，原MySQL仍active。
测试存储/库已按正常退出流程清理，不删除业务数据；产品源码及既有断言未修改。
当前版本复验仅执行一次，没有失败后重试。
三份二进制哈希构建后与测试后相同。
另经[独立结果核对](verified.txt)逐轮重算最终SHA、确认分片、提交前后状态和发布图，
并核对完整故障分布及源码/入口哈希。

memory.high累计80521未增加，max/oom/oom_kill均0，终态可用内存2036MiB。
离散资源观察不是峰值RSS或长期稳定性结论。执行入口只读复核未发现实质问题；
实际结果仍以完整日志和逐轮记录为准。

## 断言范围

HTTP覆盖角色/项目ID替换、同分片幂等和冲突、续传、重复完成、Range字节、
固定历史版本、过期上传冲突、移动/软删/还原/成员变化和pending状态。
其内部T-07是旧上传冲突，T-09是未解析状态，**不等同于需求同号的旧索引发布及解析失败验收**；
索引/解析由[当前C++](../cpp-regression/README.md)及[当前解析HTTP](../processing-regression/README.md)另证。

20轮包含分片临时文件fsync、合并fsync、对象rename、DB提交前、DB提交后、HTTP回包前。
每轮用真实子进程退出86和重启，核对已收分片、持久状态、历史原文、当前下载、
DB发布图、最终SHA和无重复版本；回包丢失要求复用原结果。
这是可控服务进程中断，不是整机断电。没有运行解析worker，不证明中断后的正文索引已完成。
M5真实业务任务、全部引用语义评审、生产部署与完整演示仍未完成。
