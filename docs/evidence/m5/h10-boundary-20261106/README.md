# H10 未显式引用依赖的撤销检查入口

2026-11-06，基线`d38f378`。仅补测试侧交错流程，不改产品代码、提示词或冻结业务输入。
需求A-03/T-08要求：进入过模型上下文的资料，即使未被显式引用，也应限制旧报告访问。

## 实现边界

[benchmark_h10.py](../../../../test/e2e/benchmark_h10.py)复用现有execute/Agent/MCP/C++。
在原DeepSeek `_send`前检查实际序列化请求的成功工具结果，匹配固定需求/设计全文和版本ID，
仅记录请求SHA-256、字节数、匹配来源。原请求和响应不改写，原sender/费用上限不变。
记录只说明发送前观察；需与对应sequence、request_id、字节数、成功model_result和usage联合核对。
不保存原始请求，不能从哈希事后重构内容；不是通用DLP或远端数据删除证明。

仅当原草稿采集完整、需求正文与设计正文均进入成功模型请求、需求有引用且设计无显式引用，
测试操作者才通过隔离夹具管理员的普通HTTP接口撤销design@1批准。原快照先保存且不覆写，
随后读取原报告、Markdown、run/result；要求三个入口409且无报告内容标记、报告数仍为1，
设计仍在服务端记录的依赖中、无该任务归档计划。不会重生成、修改引用或确认归档。

CLI只对H10启用此流程。模型completed与`h10_boundary`分开；前置不满足或边界失败返回1，
保留原模型结果，不自动重跑。边界通过不填写人工业务判定，也不代替全部引用语义评审。
完整产物0600、父目录0700；密钥仍在启动夹具前从环境弹出。

## 离线验证与失败保留

[H10测试](../../../../test/e2e/m5_h10_revocation_test.py)只替代供应商网络，真实运行Agent、
stdio MCP、C++/隔离SQL。四项覆盖：未引用设计正文撤销后阻断；仅元数据不伪造正文前置；
显式设计引用原样保留且不冒充未引用；正文所在请求失败不当作成功发送证据。
[CLI回归](../../../../test/e2e/m5_benchmark_cli_test.py)新增completed草稿但H10前置不足的非零退出检查。

- [最初4项RED](red.txt)：空实现均失败，原因是交错流程未运行。
- [入口RED](cli-red.txt)：旧入口仅凭completed返回0，新增断言要求1而失败。
- [首次GREEN尝试](first-green.txt)：H10 4/4通过；随后CLI两个集成用例在模型前失败。
  根因是测试组合共用临时库，WorkerE2E/CLI多次创建固定`worker-e2e-admin`冲突。
  该账号创建函数要求新建成功，不复用已有账号。没有改变业务约束掩盖冲突。
- 修正：CLI每次创建独立随机测试账号；共享WorkerE2E仍默认原名称，仅允许CLI覆写初始化身份。
  重启重连也沿用该身份。原归档审核仍使用私有访问文件中的实际账号，不改变确认主体。
- [最终回归](verified-journal.txt)：H10 **4/4，10.216秒**；CLI **6/6，6.418秒**。
  两个模块串行同库执行通过，证明账号冲突已解决。早期轮次不累计分母。
- 另用非默认账号运行已有C++重启/手动重试用例，见[命令](restart-command.txt)与[日志](restart-journal.txt)。

只读独立审查无阻塞发现；重连身份一致性的建议已采纳。此处网络仅MockTransport，
不构成真实H10业务验收；CLI归档回归内的synthetic reviewer不是用户确认。
30项台账不因这些离线检查增加完成数，也不新增真实模型token。

## 环境与复现

原ECS串行运行于smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、
Swap0、Tasks256、IO5MB/s及200IOPS；nobody/nogroup、Nice10、UMask0077、NoNewPrivileges，
RuntimeMaxSec600、TimeoutStopSec5、KillMode=control-group。离线单元禁外网，仅允许localhost。
没有编译或载入向量模型；原MySQL不动。低于512MiB可用内存时停止本轮单元。

[首次组合命令](command.txt)、[最终组合命令](final-command.txt)、[指纹核对命令](fingerprint-command.txt)、
[最终指纹与资源](postflight.txt)。45条指纹与运行副本一致，除CLI和测试账号初始化外，
原H09基线记录保持一致；新增测试文件另列。迁移PASS不计入用例分母。
P0/M5仍未完整验收；实际模型测试及业务评审另行记录。
