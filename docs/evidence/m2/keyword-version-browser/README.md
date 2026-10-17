# M2 关键词版本断言修正与组合复验

2026-10-18，基线 `e9b72655ba17611ee4f94be751b7b041c11d7b0d`。
只修改测试；生产搜索、编码器、模型、配置、超时和批准范围均未改变。

## 原因与最小修正

上一轮组合失败于 keyword 查询 `Replacement` 期望 1 条、实际 2 条，
原失败保留在 [staged-encoder 日志](../staged-encoder/browser-combination.txt)。
SQL 使用 `MATCH ... IN NATURAL LANGUAGE MODE`，
[接口契约](../../../api/m2-search-api.md)明确 ngram 不是精确子串匹配。

本轮先运行新的真实 HTTP/C++/MySQL/解析 worker 回归，不启动浏览器和模型：
同一人工语料实证返回的是 recovery 新版与 internal 当前版本，非旧版复活或
跨项目泄漏。keyword 允许读取有权限的内部文档；hybrid 仍只接受已批准当前版本。
回归 **1/1，4.692 秒**，单元退出码 0；原始日志见 [keyword-api.txt](keyword-api.txt)。
同时断言新版解析前旧正文退出检索、解析后新版可发现且旧版仍排除、未批准 hybrid 空。

浏览器仅将这一项 keyword 精确总数要求替换为非空、新版本 ID 出现、旧版本 ID
不出现，并继续核对状态栏与实际卡片数量一致。其余精确数量、批准、撤销、降级、
固定来源和版本断言保持不变。新增 API 用例中的 2 条是固定语料的行为刻画，
另有完整文件/版本身份集合断言，不是把失败浏览器中的 1 直接改成 2。
独立只读审查无 Critical/Important 项。

这是对上一轮真实失败的测试修正；新增 API 用例是行为刻画，未声称它自身经历
生产缺陷的 red/green。没有生产代码变更，也未重跑无改动的完整 C++/Python/JS 套件。

## 运行边界与复现

当前 ECS，同一 smartdocsvectorcheck.slice，串行执行：MemoryHigh=1200 MiB、
MemoryMax=1600 MiB、SwapMax=0、CPUQuota=100%、TasksMax=256，磁盘读写各
5 MB/s、200 IOPS。非 root nobody/nogroup，Nice=10、KillMode=control-group。
独立临时 MySQL 使用原脚本的 32 MiB buffer pool，未修改原业务库。

在同样受限单元、已构建的干净执行副本中运行：

```bash
PYTHONPATH=python scripts/test-with-mysql.sh -- \
  /path/to/parser-venv/bin/python -B test/e2e/m2_search_version_test.py
```

API 单元 RuntimeMaxSec=300；浏览器组合单元 780 秒，独立编码监听单元 900 秒。
编码监听使用 systemd-socket-activate --accept --inetd、0700 目录及 0600 socket，
PrivateNetwork=yes、RestrictAddressFamilies=AF_UNIX、NoNewPrivileges=yes、UMask=0077。
浏览器单元保留 Linux 沙箱所需设置，不冒称它也禁止 IP sockets。
运行前核对没有其他重型测试，不能把以上命令当成不受限运行建议。

组合使用 `hybrid_browser_test.py`、`SMARTDOCS_ENCODER_SOCKET`、
`SMARTDOCS_BROWSER_BIN`、`SMARTDOCS_WEBDRIVER_BIN` 和
`SMARTDOCS_HYBRID_BROWSER_OUTPUT` 指向本轮独立测试资源。
只用人工文本，未读取密钥或调用 DeepSeek；没有增加 swap 或提高资源限额。

## 组合结果

单次组合 **0/1，197.154 秒**，单元退出码 1，详见
[browser-combination.txt](browser-combination.txt)。首次编码及唯一一次显式 API 重试
均 embedding_timeout，失败已持久化，止于向量发布完成断言；没有执行到本轮修改的
浏览器关键词断言，也没有生成本轮搜索截图。不能拼接上一轮来源成功与本轮 API
成功来声称组合通过，不能把代码审查当作浏览器运行验证。

这轮说明上一轮重试后的局部成功并不稳定，正式串行编码器仍受组合冷加载限制。
本轮只有关键词 API 行为与测试修正得到独立证据，浏览器修正的端到端绿色结果仍待
验证。下一步先定位新两阶段编码器在组合负载下的具体耗时，不原样重复整场或抬高限额。
M2 仍未完成阶段验收，M3–M5 未开始；不会因测试预期修正而关闭 M2。

memory.high 从 52629 到 55921（+3292），max/oom/oom_kill 均为 0。
失败结束时编码监听仍在寿命内正常运行，随后显式停止全部本轮临时单元；
原 MySQL active，MemAvailable 1886520 KiB。准确退出码、限额和清理终态见
[unit-status.txt](unit-status.txt)。脚本按既有流程删除本轮隔离临时数据库，
仅含人工测试数据，不影响原库；该临时数据不保留恢复副本。
被测测试文件及未改动生产关键源码的 SHA 见 [source-sha256.txt](source-sha256.txt)，
已用 cmp 核对执行副本一致。
