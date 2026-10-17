# M2 C++ 向量任务与分批发布验证

2026-10-17，业务基线 `4c8357b3f1c97ed7ec044d54091bb0cf310cf796`。
实际被测改动文件校验值见 [source-sha256.txt](./source-sha256.txt)，
迁移和完整测试输出见 [cpp-regression.txt](./cpp-regression.txt)。

## 结果和范围

- 完整 C++ 回归 **135 passed / 0 failed / 0 skipped**，包含原有文件/上传/下载/
  权限/解析/检索回归、新增 6 项向量集成测试和 2 项向量结构边界测试。
- schema 5 在隔离临时 MySQL 创建与重放成功。旧 schema 2 的 103 个文件升级
  后保留原版本；另构造其中一个 schema 4 已发布、获批的版本，验证迁移为其
  排入向量任务而不制造向量结果，且共享 pending 总数仍为 100。
- 分批协议测试覆盖 9 个片段：先交 8 个仍为 processing，再交最后 1 个才为
  completed；相同批次重放不增行，异结果冲突。故障点异常使行、游标、状态回滚。
- 覆盖未批准不领取、解析前批准/解析后批准均可入队、撤销后重新批准仍拒绝旧
  token、人工重试/中断恢复、跨任务类型隔离、项目角色撤销、取消与失败重放、
  新版本/删除/unavailable 取源和发布拒绝、共享队列满、4096 窗版本预算。
- 向量校验覆盖错误 ID/摘要、顺序、区间缺口、维数、零向量、布尔值、非有限值、
  错误修订、Unicode 码点与字节区别、每块 64/每批 128 窗上限。
- 独立只读审阅未发现关键/重要代码问题；指出的 schema 文档滞后已同步至 5。
  `git diff --check` 与两个 shell 脚本的 `bash -n` 通过。

集成测试调用真实 Application 路由/JSON body handler、C++ service 和隔离 MySQL，
不是这次新做的 TCP→Python→真实模型端到端测试。单位向量是刻意构造的协议夹具，
不证明模型质量；真实模型的独立覆盖证据仍在 [编码器验证](../bounded-embeddings/README.md)。
数据库持久化与全部片段完成门槛已经实现，但 Python 调度和混合检索仍待接入。

## 执行过程

首次 RED 单元 `smartdocs-m2-vector-red` 为 0 passed / 3 failed / 0 skipped：
其中一例因向量路由缺失，另外两例发现夹具先创建无实际对象的文件再启动应用，
被启动校验正确标记 unavailable；调整为启动后创建测试元数据。不得将三例都
描述成已观察到预期的向量功能失败。

首轮 green 编译成功并建成 schema 5，但升级夹具设置批准标记时遗漏批准人和
时间，被既有数据库约束拒绝；补齐夹具，未更改业务批准约束。最终单元
`smartdocs-m2-vector-green-2` 从 19:44:37 CST 启动，最终 Result=success /
ExecMainStatus=0 / ActiveState=inactive。命令：

```bash
make -j1 bin/smartdocs_tests &&
scripts/test-with-mysql.sh -- stdbuf -oL ./bin/smartdocs_tests
```

执行副本 `/tmp/smartdocs-safe.h4SzvX/repo` 复用增量构建产物，不是干净 checkout；
运行期间不改副本。结束后 code、db、scripts、test/unit、test/integration 逐目录
diff 一致；mysql_test_support.cpp 和迁移脚本逐字节一致。归档输出从实际日志的
schema_version 行起提取；完整临时日志 `/tmp/smartdocs-safe.h4SzvX/vector-green-2.log`
未删除，但临时目录可能日后被系统清理。

始终仅一个重型单元：nobody/nogroup、MemoryHigh=1200M、MemoryMax=1600M、
MemorySwapMax=0、OOMPolicy=stop、CPUQuota=100%、Nice=10、TasksMax=256、
RuntimeMaxSec=900、PrivateTmp 并绑定上述执行副本，/dev/vda 读写各 5M 和 200 IOPS。
启动时核验 User、MemoryMax 和 CPUQuota；退出后 transient 配置可能已回收。
MySQL 测试实例禁用网络监听；此轮没有模型推理单元，也未配置网络隔离来冒充
远程外发测试。可用内存采样约 1.6–2.0 GiB，不宣称是全程最小值。

生产 mysql.service 结束后仍 active，没有升级原业务库；没有调用 DeepSeek 或
其它远程推理 API、没有读取密钥、没有启动常驻向量服务。没有重跑浏览器、真实
Python worker HTTP 闭环或大文件性能验收。schema 5 部署必须先停服务并备份，
失败恢复和接口边界见 [处理 API](../../../api/m2-processing-api.md)。M2 尚未完成。
