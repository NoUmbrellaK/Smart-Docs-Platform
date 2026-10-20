# A32：模型结果拒绝阶段的私有诊断

2026-10-20，基线 `058c722d86226cb0e0d7ffa732bdb11f64e611e8` 加本提交。
[A31](../live-public-20261020/README.md)已定位到供应商返回后的执行器处理阶段，
但缺少具体拒绝分支，原始回答也未保存。本增量补充后续运行的脱敏观测，
**不是修复或重放 A31 的实际失败，也不把 A31 的0/1改为通过。**

## 变更边界

复用现有私有0600审计，增加固定枚举的 `model_rejection`：调用序号、校验阶段、
finish_reason、正文/工具列表的形状和封顶5的工具数量。普通CLI/网页错误码仍为
model_invalid，JSON/引用/工具/预算校验不放宽，密钥、正文、参数、ID和异常文本不落日志。
审计失败收敛为dependency_failed；已有不确定写入和完成请求仍走原回执协调。
离线汇总检查新事件配对和字段，兼容缺少诊断的历史轨迹，不据此猜测历史原因。
字段说明见[受控轨迹](../../../api/m3-deepseek.md#模型结果拒绝诊断)。

按照最小实现原则只改执行器、CLI接线与已有汇总器；无新增依赖、适配器参数、
系统提示、C++、schema或前端变更。没有读取key、发起API调用、生产部署或重新编译。

## 测试与审查

- [RED](./red.txt)：3项测试均未通过，1.562秒；9组响应缺少拒绝事件、
  审计失败未映射及汇总器不支持新事件，合计10个failure和1个error。
  不是11项业务任务，也没有真实模型参与。
- [定向GREEN](./green.txt)：3/3，1.551秒。真实适配器使用HTTPX本地传输，
  真实执行器/MCP SDK/回环HTTP接收器/私有审计/汇总器运行；后端为本地协议夹具，
  不冒称真实C++/SQL或真实供应商链路。
- [中间完整MCP回归](./intermediate-regression.txt)：106/106，76.176秒。
- [最终串行回归](./final-regression.txt)：MCP/执行器 **107/107**，76.649秒；
  Python **114/114**，20.779秒；失败/跳过0。新增多轮检查已包含在107项中，
  不把定向、重复回归和子用例叠加成业务任务数。
  SDK在刻意调用未列出工具的既有负例中打印提示，测试通过，原日志保留。
- 独立只读审查没有Critical/Important问题；已落实两项测试建议：
  第四轮拒绝仍配对前3轮工具及全部usage；输入6个工具调用时诊断计数仍封顶5。
  新增多轮响应全为人工夹具，不是A31原始响应，也不证明真实模型格式缺陷已解决。

9组响应覆盖非法最终JSON、额外最终字段、length、未知字符串/非字符串结束原因、
非法正文类型、调用ID、调用对象及超额批次。另验证审计落盘失败不重试、
未知字段/枚举/计数拒绝、重复/孤立/错序事件拒绝、诊断后继续调用或声称完成拒绝、
缺终态保持不完整、旧轨迹诊断为null。模型正文、秘密标记及错误文本不进入诊断。

## 运行约束与复现

在当前ECS既有安全副本 `/tmp/smartdocs-safe.h4SzvX/repo` 中串行运行，
共用 `smartdocsvectorcheck.slice`：CPU100%、MemoryHigh1200MiB、Max1600MiB、
SwapMax0、磁盘读写各5MB/s及200IOPS；nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges、KillMode=control-group、TimeoutStopSec5。
所有单元设置IPAddressDeny=any、IPAddressAllow=localhost，只有本地协议接收器，
未启动MySQL、编码器或浏览器。

RED/GREEN单元RuntimeMaxSec120；中间完整MCP回归180秒；最终串行回归300秒。
最终单元命令体（须置于上述受限单元，不直接在ECS无限制执行）：

```sh
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
```

临时路径仅描述本次已准备环境；新环境须按仓库固定依赖准备。
测试仅清理自身TemporaryDirectory内的人工审计文件；没有删除业务数据或真实历史轨迹。
开始MemAvailable2292000KiB，回归期间两次采样2200932/2205744KiB，结束2245200KiB；
这些不是连续最低值。memory.events开始/结束high80521、max/oom/oom_kill均0，
无新增内存节流或OOM。最终MainPID=0、slice TasksCurrent=0，原mysql active。
[环境与源码指纹](./environment.txt)记录5个改动源文件与执行副本逐字节一致。
成功单元随后已被systemd回收；后读show的默认属性不是运行配置，文件已明确标注，
保留的RED单元仍可核对隔离参数。最终退出证据以回收前的完整日志为准。

## 未完成

A31真实错误的确切原因仍未知；下一次真实运行须单独明确限定批准，
不能把“补了诊断”当作重跑收费测试的授权。不自动改提示词、启用宽松JSON修复、
提高token预算或增加模型重试。M3/P0仍未验收，30项业务与实际引用评测仍未执行。
