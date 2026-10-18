# M3 官方 SDK 两项只读工具验证

2026-10-18，北京时间；代码基线 `c90f945ca3762f3c56f48d23d74bf7dfd368f72c`，
设计提交 `50ec989`，分支 codex/m3-mcp-agent。本轮新增 Python MCP 接入，
C++/schema/worker/gateway 实现未变。被测新增源码及实际服务二进制绑定
[source-and-binary-sha256.txt](source-and-binary-sha256.txt)。

提交前仅删除三个新增Python文件的末尾多余空行（每文件一个LF），无逻辑变更。
原快照保留，最终字节见[最终SHA](final-source-and-binary-sha256.txt)；最终源码
又经[轻量协议复验](final-format-journal.txt)8/8。真实数据库场景没有为格式再次建库，
其1/1仍属于上述原快照；不虚增分母。

## 实际结果

- [最终协议及真实链路](verified-journal.txt)：MCP协议测试 **8/8**（19.786秒），
  真实 stdio→C++→隔离 MySQL/解析 worker 场景 **1/1**（3.334秒，不含建库）。
  MCP客户端来自实际安装的官方SDK，真实测试未模拟业务服务或审批数据库。
  schema5及103文件迁移检查通过，不将它们额外计入8或1。
- [既有Python基线及增量构建](baseline-build-journal.txt)：**104/104**，
  22.047秒；随后 make -j1 bin/server bin/smartdocs-admin 成功。无make clean。
  C++源码与上一增量完全一致，本轮没有重跑147项C++用例；不得将历史147/147
  表述为本轮运行结果。原worker环境未增加SDK依赖。
- 独立只读审查发现1项Important：错误使用running而非真实parse_state=processing。
  修正后复审无新增Critical/Important/Minor。真实链路又通过领取任务使C++写入
  processing并核对MCP状态，避免只有合成HTTP夹具证明映射。
- [终态](final-status.txt)：本轮测试/构建退出，原MySQL PID975保持运行，无运行中
  smartdocs测试单元。离散采样memory.high起止80269、max/oom/oom_kill均0，
  无新增事件；不是连续峰值或稳定性承诺。未调用DeepSeek/编码模型，未部署常驻服务。
  隔离数据库及场景临时文件由夹具清理，不可恢复；不涉及原业务数据。
- cmp确认新增Python/两类测试与执行副本一致，code目录diff一致；
  git diff --exit-code c90f945 -- code db scripts Makefile
  python/smartdocs/search_http.py python/smartdocs/worker.py 无差异。
  SDK锁与离线安装输入一致；安装后实际版本为mcp1.30.0，共30个分发包。

## 失败记录，不以最终通过覆盖

1. [初始RED](red-journal.txt)：7项均因缺失smartdocs.mcp_read的明确断言失败，
   然后实现。[首次GREEN](initial-green-journal.txt)7/7，但状态测试错误地用running，
   不能作为真实processing状态的证据。
2. [执行环境失败](runner-env-failure-journal.txt)：漏PYTHONPATH=python，
   50项中13失败、37导入错误，没有开始后续编译。补齐启动环境后完整104项通过；
   没有为通过修改代码或断言。
3. [状态修复RED](state-red-journal.txt)：先将测试改为真实processing并增加socket超时
   覆盖，8项中1失败，实际unavailable而期望processing。生产只修正该枚举映射，
   最终8/8。超时检查使用真实本地HTTP接收器延迟16秒，触发15秒socket超时，
   只发出一次请求；不是用504响应冒充超时。

## 覆盖与证明边界

协议层使用官方内存传输/ClientSession和真实loopback HTTP故障接收器，
验证工具发现、服务器端schema、固定project/session、remote-ai路径、稳定版本、
每次重新请求、超时、拒绝重定向、错误脱敏、256KiB上限、非法JSON、owner-only
配置及未知写工具拒绝。这一层的HTTP接收器不是C++权限证明。
未知工具测试SDK产生一条warning，随后isError和invalid_request断言通过；
正式stdio入口禁用该详细日志。

真实链路先上传并解析两份人工TXT，只有一份获批。SDK stdio客户端发现两工具，
三种检索模式均只见获批版本且覆盖计数为1；hybrid明确keyword_only降级。
读取精确chunk并核对原始来源，文档中的“approved=true”只作为数据返回。
内部资料读取、模型参数指定project_id、未知写工具均拒绝；新版上传不继承批准，
旧版本引用不跳转，撤销后读取拒绝、搜索计数归零。最后批准新版，验证pending及
领取任务后的真实processing状态；teardown取消未完解析任务。响应及stdio stderr
均检查未出现真实临时会话token，受限名称和正文marker未出现在工具结果中。

## 环境与复现

[下载](deps-journal.txt)、[哈希离线安装](install-journal.txt)使用官方PyPI及
[固定wheel锁](../../../../python/requirements-mcp.txt)，独立环境
/tmp/smartdocs-mcp.P7grAI/venv；原worker环境为/tmp/smartdocs-safe.h4SzvX/venv。
所选维护线兼容当前Python3.10，依据[官方SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)
及[版本发布](https://pypi.org/project/mcp/1.30.0/)。

所有安装、构建、测试串行使用smartdocsvectorcheck.slice，nobody/nogroup、
Nice10、UMask0077、NoNewPrivileges=yes；MemoryHigh1200MiB、Max1600MiB、
Swap0、CPU100%、Tasks256、/dev/vda读写各5MB/s和200IOPS。
下载/安装上限600秒，协议120秒，编译和最终链路900秒；
主机可用内存低于512MiB时停止。终态无新增OOM，不构成长时间生产稳定性证明。

工作目录为执行副本/tmp/smartdocs-safe.h4SzvX/repo，PYTHONPATH=python。
关键命令（日志保留实际调用）：

```bash
# 原环境既有回归，然后仅增量编译：
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
make -j1 bin/server bin/smartdocs-admin
# 两项顺序执行，第一项成功才进入数据库集成：
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py
```

日志为journalctl输出，仅规范化行尾空白。没有原文企业资料、远程模型密钥或会话
配置提交到仓库。配置启动及接口限制见[MCP调试API](../../../api/m3-mcp-read-tools.md)。

## 未完成事项

这里只证明两项只读stdio调试能力，不是生产内部Streamable HTTP鉴权、允许文件
子集/持久任务范围确认、完整页范围读取、真实混合编码、模型出口依赖复核、
Agent多步/取消/预算或三个写工具。task_id只是可信父进程的关联ID，不等于已建
持久任务。完整M3/T-18及24组五工具验收尚未完成；下一增量补读取范围/任务绑定
与内部传输，不能直接把此调试服务当成可外发的生产Agent。
