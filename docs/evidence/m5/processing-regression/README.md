# 当前解析与检索 HTTP 回归

2026-10-20，源码基线`da979e4`。补验当前版本的Python及真实解析发布/检索链路，
回应[验收索引](../acceptance-index.md#版本绑定与发布复验)中的M2后端版本差异。
不改产品、测试断言、依赖或schema；不是30项真实Agent业务结果。

## 源码与执行环境

[指纹](source-sha256.txt)绑定164个逐字节相同的当前工作树/安全副本文件：
C++、迁移、构建脚本、Python源码、完整test/python、实际测试入口/辅助模块和使用的夹具。
server/admin哈希与[前一轮构建核验](../cpp-regression/build.txt)一致；
本轮没有重新编译，不冒称干净构建。

最初宽范围核对遇到执行副本缺少未使用的embedding依赖安装清单，随后按实际运行文件
明确范围，未安装新依赖。另发现缺少既有`test_agent_benchmark_fixtures.py`，
原样补入副本后重新核对全部164项，不跳过该测试。以上是执行副本预检，不是产品测试失败。
依赖清单、夹具生成器/说明/字体资产未全部复制，因此不声称副本等于完整checkout。

[运行配置](running.txt)：Python3.10.12、pypdf6.21.0、typing_extensions4.15.0；
当前ECS、安全副本`/tmp/smartdocs-safe.h4SzvX/repo`。
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges，CPU100%、
MemoryHigh1200MiB/Max1600MiB、Swap0、TasksMax256，
/dev/vda读写各5MB/s及200IOPS。仅允许localhost，RuntimeMaxSec900、TimeoutStopSec5、
KillMode=control-group。主机可用内存低于512MiB时停止本轮单元，不提高限额。

## 固定执行顺序

单元`smartdocs-m5-processing-regression.service`设置`PYTHONPATH=python`、
`PYTHONDONTWRITEBYTECODE=1`，以`set -eu`顺序执行：

```sh
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_worker_test.py
bash scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B \
  test/e2e/m2_search_version_test.py
```

两个HTTP套件分别创建新的隔离MySQL，前一套正常清理后才启动后一套。
不连接业务库；测试存储、账号、worker凭据均由夹具临时创建并清理。
MySQL初始化的空root密码警告仅属于禁用网络的临时测试库。

## 实际结果

[完整日志](journal.txt)，23:25:29—23:29:35（Asia/Shanghai），总墙钟4分6秒，
包含两次建库/迁移与清理：

| 套件 | 结果 | 测试耗时（不含建库） |
| --- | --- | --- |
| 完整test/python | 124/124，0失败/错误/跳过 | 20.704秒 |
| 真实worker | 7/7，0失败/错误/跳过 | 15.104秒 |
| 当前版本检索HTTP | 1/1，0失败/错误/跳过 | 2.035秒 |

每套隔离库另有10条迁移PASS，共20条日志标记，不计入上述测试分母，
也不当作20个不同迁移场景。未重跑或放宽断言。
[逐名静态核对](verified.txt)确认124个Python源码测试与日志精确一致，
7个worker测试无遗漏，检索脚本仅执行1个目标测试；源文件和二进制指纹未变化。

[终态](final-status.txt)：success/exit0、inactive/dead，测试cgroup已移除，
无nobody身份的mysqld或server残留，原MySQL仍active；测试存储/数据库由正常夹具退出清理，
不影响业务数据。两个二进制哈希运行前后相同。
memory.high累计80521未增加，max/oom/oom_kill为0；终态可用内存2125MiB。
离散资源观察不能替代进程峰值RSS或长期稳定性测量。

## 判定范围

- 完整test/python包含解析、编码协议/故障、检索网关及评测工具测试；
  编码器采用协议夹具/替身，不据此声称本轮运行了真实向量模型。
  其中固定任务结构检查和评审/采集测试不计入30项业务完成分母。
- worker的7个测试使用真实C++/MySQL/HTTP/本地解析子进程：
  TXT、Markdown、英文/中文PDF的正文、物理页、行、字符偏移、SHA及固定版本片段；
  扫描/损坏/不支持/空正文失败后仍可管理下载；
  600000字符/500片段及分页、用户JSON限制不被放宽；
  取消、旧attempt、worker锁和C++重启后的人工重试，以及内部接口鉴权。
  多种格式子场景不另增测试分母。
- 版本检索脚本显式只运行1个新增用例，不通过继承再次执行7个worker用例。
  新版未解析时旧正文立即退出，解析后只匹配当前版本；
  保留MySQL ngram的额外匹配断言，未获批准的资料不能进入hybrid结果。
  此处没有获批真实向量，不当作混合语义质量验证。

没有浏览器、完整M1 HTTP或20轮中断重跑、常驻部署验证、真实模型调用及引用语义评审。
不读取DeepSeek key，不复用已经消费的三次限定授权；M5及P0仍未完成。
