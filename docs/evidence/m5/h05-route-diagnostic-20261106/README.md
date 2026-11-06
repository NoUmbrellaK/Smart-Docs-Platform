# H05 冻结输入的全链路离线诊断

2026-11-06，基线 `4a97a11`，生产实现与第二次 H05 实测保持一致。
本增量仅扩展现有执行测试，不修改产品行为、冻结输入、评测判据或真实结果台账。
目的：把此前的内存 MCP/假 HTTP 定位扩展到真实 C++、隔离 SQL、stdio MCP 和供应商请求序列化。

## 范围与解释

测试原样使用冻结问题：

> 本次只检查元数据搜索的第一页（最多10项），不翻页；报告必须说明没有完整核查。

没有替换成此前局部测试中更明确的“不读正文”句子。模型网络用
`httpx.MockTransport` 替代，按脚本返回一次元数据首页查询、一次报告保存。
模型工具选择是测试给定的，不是模型自主遵循指令的证据。
没有真实 API 请求，不读取/加载真实 key，也没有归档确认或执行。

检查点覆盖：

- 两次实际供应商序列化请求均完整保留原始用户问题。
- 返回 page=1、page_size=10、has_more=true、next_page=2、truncated=true；
  11 份资料中返回文件 ID 排序前 10 份，不含正文 chunk/chunks。
- 工具仍公开 search/read/save 三项：不存在由本句自动生成的服务端 metadata-only 策略。
- 受信清单 revision/items 与任务绑定一致；管理 API 的 remote_ai_approved 不被误当成模型上下文字段。
- 只执行 search/save；草稿保存 11 份选择版本、首页 10 份来源依赖；
  六项均 uncertain，无引用，业务判定仍为空。
- 沿用私有文件权限、无 key/cookie 泄露和不发送 expected/fixture_mapping 的断言。

该诊断用于排查“原句未传到供应商请求”和“后端无法保存这种草稿”的假设，
不能证明真实模型的内部解释，也不能代替 H05 业务验收。
结合两次真实失败，后续应评估可执行的检查限制表达及最小约束路径；
不能只凭提示文字存在，宣称已有硬性限制，也不修改冻结案例降低判据。

## 测试修正与审查

初次运行 9 项中 2 项失败，第二次 1 项失败；均保留日志，不记成产品缺陷：

1. 旧查询测试把最后一条消息当成工具结果；实际最后还有运行预算 system 消息。
   改为按 role=tool 定位，原结果和引用断言保留。
2. 新测试错误地拿管理清单完整对象对比模型上下文；改为比较实际协议的 revision/items。
3. 新测试两次调用都使用 step1，被运行器的重复 ID 检查拒绝；第二次改用 step2。

测试辅助器记录并重新抛出模拟供应商断言，避免仅得到被运行器归一化后的失败码。
这些修正均在测试层，没有放宽生产验证器。
只读审查未发现 Critical/Important/Minor 问题；审查本身没有运行测试，
不将其结论当作执行成功证据。

## 证据

最终完整执行 **9/9 通过，23.101 秒**；服务运行 17:08:27—17:10:33，退出码 0。
此次验证排除了这条受控链路上原句丢失、分页标志丢失、元数据草稿不能保存的假设；
不排除真实模型对原句的不同解释，不证明提示候选已修复 H05。

- [首次命令](initial-command.txt)、[首次失败日志](initial-journal.txt)
- [第二次命令](second-command.txt)、[第二次失败日志](second-journal.txt)
- [最终命令](final-command.txt)、[资源预检](preflight.txt)
- [最终日志与终态](final-journal-and-status.txt)、[指纹与资源复核命令](postflight-command.txt)、[复核输出](postflight.txt)
- [提交前一致性检查](verification-command.txt)、[实际输出](verified.txt)

复用受限 slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256、
IO5MB/s+200IOPS。最终运行时一次采样 MemAvailable=1754716KiB，
结束采样 1889640KiB；这些是离散采样，不是连续峰值。
memory.events high 仍82471，max/oom/oom_kill 仍0；结束 TasksCurrent=0，原 MySQL active。
命令请求 IPAddressDeny=any/Allow=localhost；成功的临时单元结束后这两个属性读取为空，
不把结束时读回值当作运行中网络规则的证明。供应商使用 MockTransport 和合成 key。
37份既有运行指纹未变化；新增测试与运行副本 SHA256 均为
`0c19df0b87466b86fee65896bf8f94e9d8bd94680db81d276bfa1c1890ac9d37`。
复核输出尾部的125项结果是上一增量的历史日志，不是本次新跑的全套回归。

真实任务账本未变化：30 项分母，最新 13 项运行完成、14 项未运行、3 项准入拒绝；
26 次历史尝试、94 次请求、476089 token、73 条历史引用，人工评审仍为空。
协议夹具调用和合成 usage 不计入这些真实调用统计，M5/P0 未验收。
