# T-20：schema17 恢复后的显式任务限制

2026-11-07（Asia/Shanghai），基线 `23ccb1f9e07da72ddbc026d06f117cab5b4c38ef` 加本提交。
仅扩展既有 [恢复测试](../../../../test/e2e/m3_backup_restore_test.py)，不改产品、schema、
冻结业务输入或判据；不重新编译，不读取真实 API key，不调用远程模型。

## 范围与断言

在同一隔离备份中加入一个待执行任务，显式选择
`{"mode":"metadata","max_page":1,"page_size":2}`。全库行摘要和 storage 成对校验仍保留。
新库、新目录、新 C++ 进程启动后，Get 和 run/start 必须返回原约束，元数据第一页实际返回
获准的固定版本；越页、每页数量超限、默认每页10项、keyword、hybrid、状态读取、指定真实
chunk 读取和 range 读取共8次请求均须返回 `403 task_inspection_restricted`，不携带 data 或
样例正文，工具审计须记录一次 success 与八次 access_denied。

这些断言捕获恢复时约束丢失、误用默认值或工具边界被放开的退化。是对已有行为的恢复覆盖，
不是新产品修复或新的 RED/GREEN 实现周期。原测试的文件、版本、角色、私人报告、历史引用、
来源撤销、索引重建、断点续传和原库/备份不变检查不删减。

## 实际结果

- [关键词模式](keyword.txt)：1/1通过，25.178秒；本地编码0次。
- [向量模式](vectors.txt)：1/1通过，36.982秒；真实本地编码8次（初始/重建文档4次、查询4次），
  向量重建和关键词降级检查执行。两次分别从独立临时库开始，不拼接部分结果。
- 每种模式均保留9个存储文件、4个版本、1条报告引用、1次续传，限制恢复及8次拒绝全部通过；
  失败/跳过均为0。关键词重建后的排序变化如实记录，不承诺相关度顺序固定。
- 两次实际执行 schema17 迁移及迁移兼容性夹具；上述1/1是恢复测试方法数，不把包装器检查混算。
- 独立只读审查未发现阻断项。没有为本次测试修改产品行为，没有付费 token。
- [终态](final-status.txt)：测试进程均退出，slice TasksCurrent=0，原 MySQL active；
  memory.events high维持82471，max/oom/oom_kill均0，无新增内存节流/OOM。
  本轮临时库、备份、storage及空socket目录已清理，离线模型缓存保留。
- [postflight.txt](postflight.txt) 与 preflight 逐字相同，测试没有修改已绑定的代码和二进制。

## 复现与安全限制

本目录 [run.sh](run.sh) 和 [start-encoder.sh](start-encoder.sh) 是当前主机的操作记录，
必须先核对路径、权限、空闲资源和 slice 限额，不是通用一键生产恢复脚本。
两个测试串行；编码器与向量测试共用一份 CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、Tasks256、磁盘读写各5MB/s与200IOPS的预算。测试以 nobody/nogroup 运行，
Nice10、UMask0077、NoNewPrivileges、KillMode=control-group，SQL测试仅允许localhost，
编码器 PrivateNetwork/AF_UNIX，只读既有 MiniLM 缓存，实际 READY 后才运行向量测试。
观察 MemAvailable，低于512MiB时停止本轮测试，不提高上限绕过资源问题。

`run.sh` 只安排服务，不等待完成；必须查看对应单元最终 `Result=success`、
`ExecMainStatus=0`、`ActiveState=inactive` 以及完整测试日志，不能把启动成功当测试通过。
测试包装器只创建临时 MySQL，测试自身拒绝非测试 socket；不连接或停启原 MySQL。
临时库、备份和 storage 由既有包装器退出清理；模型缓存保留。

[preflight.txt](preflight.txt) 由 [fingerprints.py](fingerprints.py) 校验工作树与运行副本：
复用211文件及两个二进制绑定，额外绑定本次恢复测试；没有用旧二进制代表新产品。
实际环境见 [environment.txt](environment.txt)，共享 slice 运行中限额见 [resources.txt](resources.txt)，
编码器实际 READY 时配置见 [encoder-ready.txt](encoder-ready.txt)。编码器启停见 [encoder.txt](encoder.txt)。
临时 systemd 单元退出后可能被回收；终态文件中的空 User/Slice、infinity/默认配置不是运行时配置，
不能用这些默认值推断限额未启用。SQL 单元启动参数由本目录 run.sh 记录，未额外保存其运行中全部属性。

## 边界

本机人工小资料的恢复验证，不是生产灾备、异机/跨版本迁移、冷缓存恢复、RPO/RTO或压力测试。
测试本体耗时不包含隔离库准备/清理。报告是人工测试夹具，不计真实模型业务成功或语义引用准确率。
不重写旧H05，不改变30项评测台账，不代替用户对A01具体方案的真实网页确认；M5/P0仍未完成。
