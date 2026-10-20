# M5 隔离 HTTPS 代理验证

2026-10-21，基线 `b52eea3` 加本轮测试。新增
[m5_tls_proxy_test.py](../../../../test/e2e/m5_tls_proxy_test.py)，
复用标准库、现有HTTP夹具和四份Nginx文档模板；不改产品代码或数据库迁移。
真实Nginx终止TLS，后端为当前普通版C++和新建隔离MySQL。
两个独立本地接收器只验证Hybrid/Agent执行路由，不实现网关业务、不调用模型。

## 范围

1. 实际运行 `nginx -t`，配置可解析且证书/路径可读。
2. HTTPS客户端信任临时自签证书并验证127.0.0.1 SAN，未关闭证书校验；
   真实C++ ready=200，匿名me=401。
3. 错误Origin登录403；正确HTTPS登录返回Secure/HttpOnly/SameSite=Strict，
   随后会话能读取me。测试HTTP客户端显式携带Cookie，不等同于浏览器Cookie策略验收。
4. 78000字节合成资料经65536字节分片上传，完整下载逐字节一致，
   Range 7–31返回206、正确字节与Content-Range，匿名下载401。
5. 真实启用的内部处理路由直连401、经代理404；未知Host连接被关闭。
6. 精确Hybrid及Agent execute路由到各自接收器，保留Host/Origin/`{}`正文；
   4097字节请求返回413且不转发。任务状态及取消留在C++；
   取消带`{}`返回400/body_not_allowed，空正文查询不存在任务404。

这六项是一个隔离场景内的检查组，不是6项固定业务任务，也不累计为6次完整部署。
未测公网CA/域名、防火墙、HTTP/2客户端、限流饱和、长时稳定性、
systemd完整组件安装、真实搜索/编码/Agent执行及用户归档确认。

## 配置与依赖来源

测试从M1、解析worker、混合搜索及Agent文档提取Nginx片段，
保持location正则、内部拒绝规则、请求大小和上游选项。
替换为随机环回端口，合并http级限流zone与server级location，
重复的`client_header_timeout`只保留一次，补齐临时证书、pid、日志/缓冲目录。
未知Host的default_server/444是按部署文档的文字要求补齐的测试配置，
不能说四份片段逐字不改即可启动。配置仅供测试，不覆盖`/etc/nginx`。
上述Host分流和`-t`的含义按[Nginx请求处理](https://nginx.org/en/docs/http/request_processing.html)
及[命令行说明](https://nginx.org/en/docs/switches.html)核对。

Nginx `1.18.0-6ubuntu14.21` amd64从Ubuntu官方HTTPS取得，仅`dpkg-deb -x`解包，
没有安装、运行维护脚本、更新系统包索引或启动生产代理。
[package.txt](package.txt)记录签名InRelease→Packages.xz→deb哈希链及二进制指纹。
本机旧索引`.20`对应下载失败也保留，未把下载错误混入业务分母。
下载依赖时没有业务材料/凭据上传；测试运行阶段仅允许环回网络。

## 首轮失败与修正

[首轮日志](initial-journal.txt)：01:02:04–01:03:52，退出1。
TLS、登录、分片上传、下载/Range已执行；取消路由测试错误地发送`{}`又期待404。
`agent_routes.cpp`先拒绝非空请求体，因此修正测试，分别检查400及空体404，
没有修改后端行为。首轮没有记录实际错误正文，具体错误码由修正后的断言验证。

另加强internal断言：原测试关闭worker且访问不存在路径，无法排除C++自身404；
现使用公开合成token启用真实处理路由，直接401与代理404成对比较。
只读审查确认这两处改动符合现有契约；不能把首轮弱断言算作该对照已通过。

[第二轮日志](recheck-journal.txt)：01:04:50–01:06:32，退出1，未进入TLS断言。
启用合成worker token后，测试遗漏在管理命令前设置`SMARTDOCS_LISTEN_ADDRESS`；
`AppConfig::ValidateWorkerSettings`要求带token时监听127.0.0.1，而默认是0.0.0.0。
管理命令与server使用同一配置校验，原ServerProcess仅为后续server子进程设置环回，
无法覆盖先执行的create-user。此次日志只留exit1，未留其stderr；
补齐测试环境的显式loopback，不放宽产品校验或更换账号规避失败。

## 最终结果

[第三轮原始日志及终态](verified.txt)：01:07:24–01:09:10，退出0，
**1/1场景、6组检查通过**；[机器结果](result.json)保留实际78000字节及SHA。
三次尝试合计1成功/2失败，迁移PASS标记不加入六组分母，准备失败也不删除。
本轮没有发现需修改产品或Nginx路由模板的问题；修正只限测试夹具。
仅这一小资料场景通过，不推导吞吐、压力稳定性或部署全部完成。

[最终运行属性与脚本指纹](final-running.txt)重新核对169个运行文件：
相对首轮仅测试脚本改变，其余168个文件保持一致。
终态共享slice TasksCurrent=0，无nobody测试进程；原MySQL active。
memory.high维持80521、max/oom/oom_kill均0，可用内存2048MiB；
不表示CPU/IO未受配额节流，也不是进程峰值测量。

## 运行约束与复现

[首轮运行属性](running.txt)记录原共享slice的CPU100%、MemoryHigh1200MiB/
Max1600MiB、Swap0、TasksMax256、读写各5MB/s与200IOPS。
测试以nobody/nogroup、UMask0077、NoNewPrivileges、Nice10运行，
仅允许localhost、RuntimeMaxSec480、TimeoutStopSec5、KillMode=control-group。
各轮串行、各自新库，未接触业务库、真实API key或已消费的DeepSeek授权。
临时证书私钥、合成账号会话、存储、代理日志及数据库由测试清理，不提交这些材料。

```sh
# 必须放在上述受限非root单元内；不是无资源上限运行建议。
scripts/test-with-mysql.sh -- /usr/bin/python3 -B test/e2e/m5_tls_proxy_test.py \
  --nginx /private/unpacked/usr/sbin/nginx
```

[首轮源码关联](sources.txt)逐字节比对169个源码/资源/测试/模板并绑定server/admin与Nginx。
测试脚本后续修正与最终结果单独记录，不能将首轮脚本哈希当作最终脚本哈希。
该增量不代表全套部署或完整业务演示完成，M5/P0仍未验收。
