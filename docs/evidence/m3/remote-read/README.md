# M3 C++ 远程 AI 搜索与读取门禁

2026-10-18，北京时间。M2 基线 `c0c257bd53203fec33158dd1f70eefc0c165cc1c`，
设计提交 `4a24b3e`，分支 `codex/m3-mcp-agent`。这是 M3 第一个增量，
不是 MCP/Agent 或整个 M3 验收；不包含真实模型、浏览器、生产部署。

## 验证结果与失败记录

- [初始 RED](red-journal.txt)：新增 5 项通过真实 Application 路由访问新路径，
  旧实现 **0/5**，因缺失路由失败；没有用模拟服务制造失败。
- [首次编译加全套](combined-timeout-journal.txt)：受限单元 900 秒超时，
  exit143，没有完整汇总；不能算全套通过。观察到隔离 MySQL 的 TRUNCATE
  等待提交，资源事件未新增；不把这一观察推断成唯一原因。
- [分离编译](build-journal.txt)成功，随后按全部 147 个 TEST_CASE 名称排序，
  交替分成两组。[第一组](group-1-journal.txt) **74/74**，exit0。
- [第二组首次失败](group-2-fixture-failure-journal.txt)：25 项通过后，
  新增的候选返回前撤销批准测试失败，后续 47 项未执行。测试直接清零批准
  标记却保留批准人/时间，违反真实数据库的 `ck_file_version_remote_approval`。
  改为调用生产 FileService::SetRemoteAiPolicy 撤销指定版本批准，文件本身
  仍为 remote_ai_allowed。此处只修正测试夹具，不改生产代码或放宽断言。
- [夹具修正差异](revocation-fixture-fix.diff)仅涉及该单项测试；
  第一组不含它，第一组所测实现和测试代码均未变化。
  [修正后编译](build2-journal.txt)成功；[第二组完整重跑](group-2-fixed-journal.txt)
  **73/73**，exit0。最终两组 **147/147**，无失败/跳过。
  [名称核对](coverage-check.txt)确认实际 PASS 并集等于源码全部 147 项，无遗漏/重复。
- 原二进制与源码见[首轮 SHA](source-and-binary-sha256.txt)，修正后见
  [最终 SHA](final-source-and-binary-sha256.txt)。两次四个生产文件 SHA 相同；
  测试文件与二进制因上述夹具修正不同。不能将失败轮 25 项重复计入最终分母。

终态见 [final-status.txt](final-status.txt)：测试单元 inactive/dead、MainPID0，
无运行中的 smartdocs 测试单元，原 MySQL active（PID975）；主机可用内存约
1.8GiB。离散采样中 memory.high 始终为 80269，max/oom/oom_kill 为 0，
无新增事件。执行副本的 code、test/integration、test/unit、db、scripts、
Makefile 与待提交工作区一致，最终 SHA 复核全部通过。

## 证明范围

复用真实 C++ Application、权限/业务事务和隔离 MySQL，不模拟审批数据库。
6 项 M3 用例覆盖：三种模式排除内部名称/正文及覆盖计数、未解析获批资料发现、
新版本不继承批准、固定历史版本/片段出处、撤销后拒绝、成员/跨项目/删除/同源、
分页最多 10 项及身份/绕过参数拒绝；补验在 metadata/keyword 候选选出后通过
真实事务撤销批准，返回前再次检查并标记 scope_changed，下一次计数归零。
旧普通接口仍允许有阅读权限的内部资料。

独立只读审查没有 Critical/Important，提出补充候选返回前撤销测试的 Minor
已落实。首次静态复核遗漏夹具数据库约束，实际执行发现后修正；审查不替代实测。

每个名称单独启动测试进程，组内串行共享该组的隔离数据库。已核对名称唯一，
且没有名称是另一名称的子串，避免既有 --filter 子串匹配导致重复运行。
这属于全部用例分组覆盖，不是同一进程一次执行全套的证明；各组的 schema5、
迁移重放/部分迁移拒绝及 103 文件迁移检查不额外计入 147 项。

## 可复现执行与资源约束

工作副本 `/tmp/smartdocs-safe.h4SzvX/repo`，当前 ECS 执行，不扩容、
不清理缓存、不并发构建/模型，不读取 DeepSeek env，不调用远程 API。
编译为 `make -j1 bin/smartdocs_tests`，不 make clean。

两组完整名称分别在 [group-1-selection.txt](group-1-selection.txt)、
[group-2-selection.txt](group-2-selection.txt)。每组使用原脚本建立隔离库：

```bash
bash scripts/test-with-mysql.sh -- bash -c '
  for test_case in <该组选择文件内的完整名称，以空格分隔>; do
    stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case" || exit $?
  done'
```

对应单元：smartdocs-m3-read-red、smartdocs-m3-read-green（超时）、
smartdocs-m3-read-build、smartdocs-m3-read-group1、
smartdocs-m3-read-group2（夹具失败）、smartdocs-m3-read-build2、
smartdocs-m3-read-group2-fixed。均为 nobody/nogroup，Nice10、UMask0077、
NoNewPrivileges=yes、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
共用 smartdocsvectorcheck.slice：MemoryHigh1200MiB、MemoryMax1600MiB、
Swap0、CPU100%、Tasks256、/dev/vda 读写各 5MB/s、200IOPS；
采样发现主机 MemAvailable 低于 512MiB 即停止测试。

数据库由脚本在精确 /tmp/smartdocs-mysql.* 临时目录创建并自动删除，
不会修改原业务库；这些测试夹具不保留恢复副本。日志为 journalctl 原始输出，
只规范化行尾空白。资源采样不是连续峰值测量，不证明长期稳定或消除历史 IO 风险。

## 未完成边界

本增量不提供 MCP 协议、可信任务范围、整篇/页范围读取、模型出口依赖复核、
持久 Agent 编排、五工具合同及 M4 归档/报告事务。通过一次读取不能作为永久
外发授权。未重跑未修改的 Python/前端/真实模型测试，也未升级常驻生产服务。
下一增量按设计接入官方 MCP SDK 的只读工具和可信身份，不自研协议或权限库。

