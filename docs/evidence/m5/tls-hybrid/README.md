# M5 HTTPS 与真实离线向量组合

基线 `3151b59`，2026-10-21。本轮补[上一轮TLS真实网关](../tls-gateways/README.md)
未包含的实际编码链路：[测试](../../../../test/e2e/m5_tls_hybrid_test.py)通过私有Unix
socket使用已缓存的MiniLM，真实运行vector worker、HTTPS搜索、Agent网关、
stdio MCP、C++及隔离SQL。仅收费提供商transport用本地替代，不读取真实key或外发。
不改产品代码或数据库迁移；两个既有测试helper仅增加可选encoder socket，
默认仍保留真实不可用路径。

## 断言范围

新场景分三组，不是三项固定业务任务：

1. 三份资料均实际解析，未批准时向量worker idle，两份批准后分别真实编码发布。
   中文“如何恢复备份？”经HTTPS命中英文资料，结果为hybrid且match_sources仅vector。
   同一英文backup对照查询：普通本地keyword包含三份，AI hybrid仅含两份获批资料。
   后者直接请求C++的hybrid模式且不带向量，是关键词降级的授权对照，不冒充额外语义检索。
2. HTTPS Agent只选择其中一份版本，经stdio MCP真实hybrid读取候选；
   任务外及内部标记不得进入第二次提供商请求。保存结果后读取原版本片段和工具审计。
   两次提供商响应是本地脚本，不证明真实模型决策、语义质量或token费用。
3. 提交并解析新版后，不继承外发批准、不向量化，默认检索排除旧版；
   旧来源仍指向旧片段，原Agent结果不可继续读。撤销另一份批准后检索为空。

同一脚本第二个方法重跑原TLS关键词场景，检查未传socket的默认行为、
重复执行409及撤销批准后无新增模型响应。两方法使用不同项目，串行运行，
不把其检查组数累计为固定30项的通过数。
本轮不包含真实模型评测、浏览器、报告/归档确认或完整生产部署。

## 环境与复现

[源码/二进制指纹及编码器属性](sources-running.txt)，
[实际READY、socket权限及业务单元属性](running.txt)。
最初宽泛预检查碰到未调用的 `m1_browser_self_test.py` 旧运行副本；
没有覆盖该文件。随后核对原169项清单及本轮相关C++、Python、M2/M3/TLS测试、
M1 HTTP helper和建库脚本，逐字节一致；不宣称整个临时目录全量同步。

模型为 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，
修订 `minilm-634d0f66-fastembed0.9.0-ort1.23.2-halves128-l2-v1`。
实际服务在加载时核验固定依赖及五份模型文件SHA，再以封存的内存文件加载；
模型哈希和依赖版本见[embeddings.py](../../../../python/smartdocs/embeddings.py)。
沿用已有缓存，不下载模型。Nginx沿用[签名校验的解包版本](../tls-proxy/package.txt)，不安装系统代理。

两个单元共享原slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
Tasks256、读写各5MB/s及200IOPS；不是各分配一份内存。
均为nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、KillMode=control-group、
TimeoutStopSec5。编码器Type=notify、TimeoutStartSec190、RuntimeMaxSec900，
PrivateNetwork且仅AF_UNIX；业务RuntimeMaxSec780，只允许环回网络。
实际READY后才启动业务；可用内存低于512MiB停止自有测试，不提高上限。

```sh
# 必须在上述受限环境，先启动已缓存模型并确认READY。
PYTHONPATH=python:test/e2e scripts/test-with-mysql.sh -- \
  /private/mcp-venv/bin/python -B test/e2e/m5_tls_hybrid_test.py \
  --nginx /private/unpacked/usr/sbin/nginx \
  --encoder-socket /private/encoder.sock
```

## 首轮失败与修正

[首轮日志](first-run.txt)：01:26:51–01:29:08，两个方法1通过/1失败，退出1。
新场景真实编码、中文检索、英文授权对照、2次本地提供商响应、结果保存和固定片段
已执行，但测试错误地期待只有一条后端工具审计；实际为两条。
尚未执行后续新版/撤权断言，不能把第一组PASS算作整个新场景通过。
原关键词场景完整通过。

追踪 `mcp_read.backend_tool` → `search.search_documents`：
hybrid先做一次权限预检，编码后再读取最新状态，两次均进入C++的 `BeginToolCall`。
该方法也明确注明一个MCP调用可能有多次后端请求。
因此只修正夹具为两条search审计，并增加sequence=1/2及success状态断言；
不删除审计、不放宽产品权限、不增加重试或超时。
[复验脚本指纹及限额](recheck-running.txt)与首轮分别保存，
首轮sources中的测试哈希不冒充最终版本。

## 复验和终态

[完整复验](verified.txt)：01:29:58–01:31:55，退出0，**2/2方法通过**，
无失败/跳过，测试本体14.136秒（不含建库/迁移/清理，不是请求性能）。
新hybrid场景3组、原关键词场景3组；迁移10个PASS另计。
新场景有2次真实文档编码批次、2次本地提供商响应；
原场景4次本地提供商响应。两者远程模型调用均0，mock usage不计付费token。
同一编码器跨两轮保持运行，因此不是两次独立冷启动测量。

保留全部尝试：首轮1/2、复验2/2；新hybrid场景累计1成功/1失败，
原关键词场景2次通过，不删除失败或累计为固定业务任务。
[机器结果](result.json)绑定最终脚本，仍不宣称M5/P0完成。

[清理记录](cleanup.txt)：编码器显式停止成功，三单元MainPID均0，
共享slice TasksCurrent=0，无nobody遗留进程，原MySQL active。
仅删除本轮空socket目录；临时库/证书/会话/storage由夹具清理，原模型缓存保留。
最后 `ps -u nobody` 因没有进程退出1，不是复验退出码或清理失败。

memory.high从启动前80521至82471（增加1950），max/oom/oom_kill均0；
最终主机可用内存2055MiB。无新增OOM不等于无内存回收/IO压力，
这些抽样也不是进程峰值RSS或长期稳定性测量。
