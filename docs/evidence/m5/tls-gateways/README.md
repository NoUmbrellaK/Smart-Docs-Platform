# M5 隔离 HTTPS 真实网关组合

基线 `adc5092`，本轮仅新增组合测试，并让既有TLS客户端支持可选超时
（默认仍10秒，Agent组合使用既有执行测试的240秒）。
[测试源码](../../../../test/e2e/m5_tls_gateways_test.py)复用原TLS配置、
Agent夹具与标准库，不改产品代码、迁移、生产配置或服务。

## 实际链路和边界

真实Nginx终止TLS，经真实SearchHTTPServer、AgentHTTPServer、stdio MCP、
普通版C++和新建隔离MySQL。上传与TXT解析worker同样真实执行。
只有提供商HTTP transport使用现有 `httpx.MockTransport`；合成key只在本地夹具内，
不读取真实API key、不调用DeepSeek，也不消费或续用此前三次限定授权。

一个场景内分三组检查，不是三项固定业务任务：

1. HTTPS上传合成资料并解析；真实搜索网关连接不存在的私有encoder socket，
   明确返回 `keyword_only` 与 `query_embedding_unavailable`，没有伪造向量。
   同时检查匿名401及错误Origin403。
2. Agent经stdio MCP先搜零结果、再搜命中、读取固定片段、保存带来源的结果。
   四次本地模型响应对应三条真实工具审计；读取保存引用的原版本片段。
3. 已完成任务重复执行409；另建待执行任务后撤销该版本的外发批准，
   执行进入failed且没有新增模型响应；旧结果读取409。

保存结果中的 `model_calls=4` 是本地替代响应计数，**远程调用数为0**；
夹具生成的usage不是实际供应商token或费用。该场景不计入30项固定业务任务，
不证明真实模型质量、完整Hybrid、报告/归档或用户真实确认。
网关由服务类启动，未覆盖各自CLI的限额、信号处理或生产systemd安装。
TLS客户端验证临时自签证书SAN，显式携带Cookie；不等于浏览器Cookie策略验证、
公网域名/CA、HTTP/2、限流饱和或完整业务演示。

## 环境与复现

复用[上一轮解包的Nginx及签名校验](../tls-proxy/package.txt)，不安装系统代理。
先逐字节核对上一轮169个源文件/资源/模板与运行副本，再额外核对所有当前
Python模块及M2/M3/TLS测试，记录[源码指纹及运行属性](sources-running.txt)。
server/admin沿用已核对当前源码的普通版二进制，不使用旧工作树bin。

非root临时单元只允许环回网络，UMask0077、NoNewPrivileges、
Nice10、KillMode=control-group、TimeoutStopSec5、总时限600秒。
共享slice保持CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
磁盘读写各5MB/s与200IOPS。240秒客户端超时同样适用于ready探测；
循环次数不是约10秒启动保证，总时限由外层单元控制。
重测旧代理场景须另建隔离数据库，避免上次待解析job影响worker领取。

```sh
# 必须位于上述受限非root单元内；MCP依赖已安装的独立venv。
PYTHONPATH=python:test/e2e scripts/test-with-mysql.sh -- \
  /private/mcp-venv/bin/python -B test/e2e/m5_tls_gateways_test.py \
  --nginx /private/unpacked/usr/sbin/nginx
```

临时证书、会话、合成账号、存储、上下文、审计目录及数据库由夹具清理；
不提交私钥、会话或上下文正文。

## 结果

[组合测试完整日志](verified.txt)：2026-10-21 01:17:04–01:18:55（UTC+8），
单次运行退出0，**1/1场景、3组检查通过**，无失败/跳过或重跑。
迁移校验的10个PASS另计，不混入三组分母。
测试方法用时5.733秒，其余时间包含建库、迁移及清理，不是请求性能测量。
终态共享slice TasksCurrent=0、nobody无遗留进程，原MySQL active；
memory.high仍80521，max/oom/oom_kill均0，可用内存2043MiB。
采集命令最后的 `ps -u nobody` 因没有进程退出1，不是测试退出码。

[原代理场景独立回归](proxy-regression.txt)：01:19:30–01:21:17，退出0，
单次**1/1场景、6组检查通过**，默认10秒客户端超时不变；
[运行属性](proxy-running.txt)记录同样的600秒非root环回限制。
78000字节合成文件上传、下载及Range保持通过，两个网关接收器仍只验证分流。
终态slice TasksCurrent=0、nobody无遗留进程，原MySQL active，
memory.high维持80521、max/oom/oom_kill均0，可用内存2023MiB。
这次独立回归不抹去上一轮两次夹具失败，也不作为新的业务任务。

[机器结果](result.json)绑定两份实际测试源码的SHA。
本轮合计两个隔离场景、各一次成功，不将3组与6组解释成9项固定业务通过；
未发现需要修改产品的问题，M5/P0仍未验收。
