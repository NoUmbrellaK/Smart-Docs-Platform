# M3 受控供应商传输增量

2026-10-18，基线604bb357751bdaf72d984341993de60a139c2c6b。
接口见[受控DeepSeek入口](../../../api/m3-deepseek.md)。本轮交付固定目的地的异步
HTTPX适配器、可信本机CLI、私有脱敏审计及显式开启的公开资料E2E脚本。
沿用已有C++任务批准、版本范围、外发前复核和两个只读MCP工具；不新增依赖或迁移。

## 已验证

- [最终MCP/执行器回归](reviewed-green-journal.txt)：**38/38**，30.641秒，单元正常退出。
  包含适配器10项、CLI3项、执行器14项、MCP只读11项。供应商使用HTTPX本地替身；
  覆盖固定出口、密钥不进正文、响应/请求限额、截止期及取消、错误不重试、usage校验、
  审计独占0600/失败关闭、工具失败审计、结果白名单及默认发现不启动付费测试。
- [既有Python回归](python-green-journal.txt)：**104/104**，23.064秒，正常退出。
- 代码与执行副本六个文件SHA逐项一致，见[source-sha256.txt](source-sha256.txt)。
- C++、schema、依赖均未改，本轮没有重新构建或运行C++/数据库E2E；上一增量的
  SDK→C++→隔离库5/5不等于本轮真实供应商验证，不能混用成绩。

## RED、失败与审查

[初始RED](red-journal.txt)为33项中的10项因模块缺失失败，随后
[首次适配器测试](fixture-failure-journal.txt)遇到5项StreamConsumed：HTTPX的
json/content测试响应已预读，改用ByteStream流式夹具，不改变生产响应限额。
[适配器复验](adapter-green-journal.txt)10/10。
[CLI RED](cli-red-journal.txt)及[轨迹RED](trace-red-journal.txt)均为对应入口缺失；
[中间回归](intermediate-green-journal.txt)34/34、[协议回归](protocol-green-journal.txt)35/35。
这些中间结果不替代最终38项结果。

独立只读审查发现四点：付费脚本发现模式隔离不完整、工具异常缺少审计、任务结果
直接展开未白名单过滤、审计失败测试发送计数器不独立。前三项由
[审查RED](review-red-journal.txt)实际复现（38项中3失败），第四项强化计数器断言。
修正后最终38项通过；复审无阻断发现。审查者未运行测试、读取密钥或修改文件。

## 真实调用尚未执行

启动真实E2E的工具请求被auto-review在创建systemd单元前拒绝，理由为该次具体数据
外发缺少明确批准及充分验证；没有通过其他路径重试。只读复核：
`smartdocs-m3-provider-live.service`为LoadState=not-found、ActiveState=inactive、MainPID=0。
本轮未发出真实DeepSeek请求，**不能声称真实模型、费用或动态检索质量已验证**。

已向用户请求的最小批准范围：固定`https://api.deepseek.com/chat/completions`，
公开人工部署说明（备份名ORBIT-27）、测试问题、系统提示/工具定义和随机引用ID；
不包含真实业务资料、用户会话或正文中的API key，最多6次请求、每次最多512输出token，
可能产生API费用。API key只用于该固定HTTPS目的地的Authorization认证头。
脚本为`test/e2e/m3_deepseek_test.py`，未得到本次批准前不执行、不自动重试。

## 复现和服务器保护

执行副本`/tmp/smartdocs-safe.h4SzvX/repo`，复用原MCP/解析虚拟环境。
实际本地测试命令（分别在串行临时systemd单元内）：

```bash
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
```

单元前缀smartdocs-m3-provider-；nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges=yes、RuntimeMaxSec120、TimeoutStopSec5、KillMode=control-group。
共用原smartdocsvectorcheck.slice：MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、
CPU100%、Tasks256；/dev/vda读写各5MB/s、200IOPS。未放宽任何限额。
可用内存低于512MiB停止；最终采样1733220KiB。memory.events前后均为
high80272/max0/oom0/oom_kill0：无新增内存节流或OOM。原MySQL仍PID975。
所有本轮本地测试已退出，失败RED单元只保留终态，成功单元已回收。

一次启动协议回归的命令含`--property=UMask0077`拼写错误，systemd在创建单元前拒绝；
核实not-found后纠正为UMask=0077，未重复运行测试。真实调用的安全拒绝是另一个事件。
没有生产部署、业务资料删除或原库迁移。

## 未完成

真实公开资料E2E（待明确外发批准）、生产内部MCP认证、完整任务审计API与页面、
三个写工具及M4事务；M3、T-10/T-18和P0整体均未验收。
