# A19 固定五工具契约验收矩阵

2026-10-19；被测产品基线 `b705cef6298a68e03083d22eef72de93db3fc3bc`。
本轮针对需求7.2和13.2第3项，运行前固定25组输入/预期。仅增加测试及证据，不改产品实现，
不新增依赖、生产迁移或部署，不读取真实key、不调用远程模型。

## 固定分母与预期

各编号对应脚本`test_contract_编号_工具名_场景`，一组一个unittest测试，不把断言数量计成用例数量。
以下为**预期**，实测结论见运行记录，不可只据表格宣称通过。

| 工具 | 正常 | 非法身份参数 | 同项目其他任务所有者 | 提交新版后请求旧范围 | 发送前超时注入 |
| --- | --- | --- | --- | --- | --- |
| search_documents | 01 success | 02 invalid_request | 03 access_denied | 04 conflict | 05 timeout |
| read_document_version | 06 success | 07 invalid_request | 08 access_denied | 09 conflict | 10 timeout |
| create_archive_plan | 11 waiting_confirmation | 12 invalid_request | 13 access_denied | 14 conflict | 15 timeout |
| execute_archive_plan | 16 success | 17 invalid_request | 18 access_denied | 19 conflict | 20 timeout |
| save_handover_report | 21 success | 22 invalid_request | 23 access_denied | 24 conflict | 25 timeout |

固定输入规则（动态ID仅用于本轮隔离数据绑定，不改变预期）：

- 搜索：已获批并解析的TXT，原文`SOURCE-MARKER`，keyword查询`SOURCE`；断言精确文件和原文。
- 读取：同一固定版本的range={}，断言原文和版本不发生替换。
- 提案：当前版本/源目录、管理员已批准目标目录、expected_revision=0、policy_revision=1；
  正常结果等待确认，无operation_id且文件元数据不变。
- 执行：先由普通HTTP真实确认具体提案修订；工具只转发该操作ID，正常结果移动1个文件到预置目标。
- 报告：复用prepared_report，实际解析/读取并向来源台账准入；工具保存含固定引用的found草稿，
  查询真实保存结果核对来源版本。夹具的run/check是协议准入，不代表调用过真实模型。
- 非法参数：在有效工具参数上附加`user_id=DO-NOT-ECHO`，须MCP校验拒绝，不进入C++工具轨迹。
- 权限：同项目另一reader持自己的真实会话请求原用户的私有任务，不能读取或写入其结果。
  此列专测任务所有权，不冒充所有角色和跨项目边界的完整覆盖。
- 版本：真实提交新版`NEW-UNAPPROVED-VERSION`，新版本未批准，原任务仍绑定旧快照；工具返回冲突，
  不返回旧/新正文或把请求静默改为新版。
- 超时：仅在HTTPConnection.request发送前注入TimeoutError；检查规范timeout、仅尝试一次、无业务写入。
  不模拟已发送但提交回包丢失，不测15秒截止期或网络性能；已提交回包丢失另见A18及既有lost-reply证据。

所有组还校验SDK结构化返回和文本返回一致、绑定task_id、32位request_id、非负duration_ms及isError。
失败组不返回data/敏感标记/会话；业务快照（文件完整元数据、方案/确认/报告及对应业务审计数量）保持不变。
正常三个写工具另重放一次，得到相同业务结果、不同请求ID且业务快照不重复改变，仍计原组而非新增组。

## 实际覆盖层与验证记录

20组通过真实官方SDK stdio子进程：15组进入真实C++/隔离SQL，5组在MCP参数校验层拒绝。
另5组经真实in-process SDK服务器注入发送前超时；不是全部25组都在真实网络上制造超时。
测试过程中不安装依赖、不跑浏览器、不编译C++；复用A18受限副本二进制。

实测结果：

- [协议/执行器基线](./baseline.txt)：87/87通过，71.029秒。
- [初次矩阵](./initial-matrix.txt)：25/25通过，34.388秒；初版脚本哈希见[记录](./initial-script.sha256)。
- [最终矩阵](./final-matrix.txt)：补充业务审计重放判据后25/25通过，31.929秒；0失败、0跳过。
  两轮使用同一25组分母，不合并计成50个不同用例，耗时不作为性能验收。
- [只读审查](./review.md)修正覆盖口径并补强业务快照；仅测试/文档改动，无新产品行为。
- [资源/退出记录](./resources.txt)：三个单元均MainPID=0、Result=success、ExecMainStatus=0；
  结束时MemAvailable为1219MiB，原MySQL仍active。memory.events的high保持80521，
  max/oom/oom_kill均为0，无新增节流或OOM；限额未提高。
- [最终源码及二进制哈希](./sha256.txt)：测试与隔离副本逐字一致，四个相关Python产品模块
  亦一致；A18的12个源码/测试文件及三个被测二进制哈希全部复核通过，未重新构建。

## 复现

既有副本 `/tmp/smartdocs-safe.h4SzvX/repo`，PYTHONPATH=python，使用锁定的MCP环境：

```sh
python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
scripts/test-with-mysql.sh -- python -B test/e2e/m3_mcp_contract_matrix_test.py
```

脚本只选择25个test_contract_方法，不误执行父类的全部测试。包装器要求私有临时MySQL socket，
验证schema16/重复迁移/部分DDL拒绝/103文件升级后运行；结束清理私有库，不访问业务库。
沿用nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、15分钟单元上限、KillMode=control-group，
smartdocsvectorcheck.slice限制CPU100%、MemoryHigh1200MiB、Max1600MiB、Swap0、TasksMax256，
磁盘读写各5MB/s及200IOPS；串行执行，MemAvailable低于512MiB停止本轮单元。

## 尚未证明

此矩阵不是30项业务任务评测、50条引用语义准确率、真实模型动态决策、Streamable HTTP生产接入、
完整MCP/模型审计API或整个M3/P0验收。合法引用由原文支持与否不能仅用协议格式通过代替。
固定业务任务和真实供应商验证仍需后续完成，不因为25组契约成功就改写阶段完成标准。
