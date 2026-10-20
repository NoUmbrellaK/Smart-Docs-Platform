# A34：普通问答启用生产端 JSON 输出约束

2026-10-20，基线 `13c9985856c90fab47838741ccdb8b29c2345f49` 加本提交。
[A33](../live-public-20261020-2/README.md)真实失败在final_json阶段；
本轮补上原请求缺少的JSON输出约束，**没有重跑API，也不宣称A33已修复**。

## 最小变更

普通问答适配器设置 `response_format={"type":"json_object"}`；
执行器仅在普通问答模式追加 `READ_FINAL_SYSTEM`，说明裸JSON并给出不足证据示例。
原SYSTEM、报告与归档提示保持原样，写模式不增加response_format。
遵循[官方JSON输出说明](https://api-docs.deepseek.com/guides/json_mode/)：
参数之外还需提示与格式示例。供应商仍可能返回空内容或截断，不能推导任务必然成功。

按最小实现原则复用现有可信工具模式判定，无新增配置、依赖、C++或数据库迁移。
未改严格JSON解析、回答/引用校验、每次外发复核、512输出上限、总预算或重试规则。
没有剥除围栏、自动修补错误、伪造出处，工具定义和工具选择循环不变。
改变了普通问答的提示与请求参数，后续真实比较必须使用本提交而非混称旧版本。

## 验证

- [RED](./red.txt)：2/2失败，0.724秒；实际出站请求均缺少response_format。
- [定向GREEN](./green.txt)：2/2通过，0.558秒。
  一项检查问答/报告/归档三种可信工具集的请求参数与预算；
  另一项经真实适配器、执行器、MCP SDK及本地HTTP接收器完成4轮搜索→改查→读取→回答，
  核对每轮JSON参数、512上限、两只读工具、实际出站格式示例与固定引用。
  供应商使用HTTPX本地MockTransport，业务后端为协议夹具，不是真实C++/SQL或云模型。
- 独立只读审查无阻断项；真实工具调用和JSON模式的组合效果仍待限定验证。
- [完整串行回归](./regression.txt)：MCP/执行器 **109/109**，77.255秒；
  Python **114/114**，21.752秒，失败/跳过0。定向2项包含在109项内，不累计为业务分母。
  SDK对刻意请求未列出工具的既有负例打印提示，测试通过，原日志保留。

原有围栏/非法JSON/额外字段/截断/错误工具批次/伪造来源拒绝测试继续保留；
不以本地模拟响应通过证明真实模型会按新格式作答。

## 环境

当前ECS，原安全副本及两套虚拟环境。所有重任务在 `smartdocsvectorcheck.slice`
串行运行：CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0，
磁盘读写各5MB/s及200IOPS，nobody/nogroup、Nice10、UMask0077、NoNewPrivileges。
所有单元仅允许回环网络；未读取key或发起真实API，没有数据库、浏览器或编码器启动。
[运行中配置与4个源码指纹](./environment.txt)在临时单元回收前采集；
工作树与被测副本cmp逐字节一致，没有提高资源限额或重编译。

RED/GREEN单元120秒，最终串行回归300秒，TimeoutStopSec5、KillMode=control-group。
最终单元内顺序执行：

```sh
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
```

路径仅描述本次已准备环境；重建需安装仓库锁定依赖，不能绕过受限单元直接重载ECS。
测试仅清理自身人工临时文件，不删除真实资料或原始审计；没有生产部署。
运行前会话观测MemAvailable2253944KiB，已保存运行中采样2197088KiB，
结束2231652KiB；不宣称连续最低值。memory.events运行前观测与已保存终态
high均80521、max/oom/oom_kill均0，无新增内存节流/OOM；
最终MainPID=0、slice TasksCurrent=0，原mysql active。

## 剩余门槛

两次真实烟测仍为0/2，M3/P0未验收，30项真实业务任务与引用评测未执行。
当前仅证明出站合同和本地严格门禁不被放宽。第三次真实测试须另获明确限定批准，
且记录新提示/参数版本；不自动使用前两次授权或剩余请求额度。
