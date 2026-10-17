# M2 编码器最小运行时导入

2026-10-18，基线 `087aa91744d3b7f1a5e50276890794071d50acac`。
按上一轮阶段计时移除两处 FastEmbed 运行时导入，不新增库或改变模型架构。
直接使用已安装 tokenizers 读取固定 tokenizer.json，按已校验 SHA 的
special_tokens_map.json 注册 AddedToken；保持空白/规范化设置、无截断/填充。
该固定 map 全部值为字典，未来换模型须重新验证，不宣称支持任意模型配置。
推理直接使用同样的 NumPy float64 masked sum、mask sum、1e-9 下限，
再转回 ONNX 输出 dtype、做原 L2 归一化。

模型与依赖 SHA/版本检查、两阶段顺序、共享锁/60 秒预算、输出限额、权限和
索引修订均不变。FastEmbed 的安装及版本元数据要求仍保留；本轮只消除运行时
导入，不能据此删除锁定安装依赖。未安装生产服务或调用远程模型。

## 回归

- 先在真实阶段子进程加入 import guard，旧实现于首次真实编码返回
  embedding_dependency_unavailable：[失败记录](initial-red.txt)。守卫不伪造库，
  原模型文件、输入输出和子进程限额均真实执行。
- 最小改动后真实模型 **1/1、11 项检查通过**，包含原 10 项以及两阶段都不导入
  FastEmbed。完整日志：[real-model.txt](real-model.txt)。6 个固定样例、16 个窗口
  的全部输出 SHA 仍为 `6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`，
  期望来自旧版固定基线，不由当前实现生成。
- 本次首次/第二次编码 22.840/4.204 秒，子进程峰值 RSS 472276 KiB（约 461 MiB）。
  缓存状态未受控，测试含 import guard 包装；不是与旧运行同条件的速度对比，
  也不是整个 cgroup 的峰值或浏览器组合稳定性承诺。
- 完整 Python 回归 **83/83，15.734 秒**：[日志](python-regression.txt)。
  未重跑无改动的完整 C++/前端套件。
- 独立只读审查无 Critical/Important；确认固定 map 结构限制及安装依赖仍保留。

## 运行边界

当前 ECS 原 smartdocsvectorcheck.slice，MemoryHigh=1200 MiB、MemoryMax=1600 MiB、
SwapMax=0、CPUQuota=100%、TasksMax=256，磁盘读写各 5 MB/s、200 IOPS。
非 root nobody/nogroup、Nice=10、KillMode=control-group，重型测试串行。
真实模型单元 RuntimeMaxSec=300，PrivateNetwork=yes、AF_UNIX、NoNewPrivileges；
每个编码请求仍共享原 60 秒预算。Python 回归单元 120 秒。

受限单元内部复现命令：

```bash
/path/to/model-python -B test/e2e/m2_embeddings_test.py /path/to/model /path/to/model-python
PYTHONPATH=python /path/to/parser-python -B -m unittest discover -s test/python -v
```

生产入口不装测试 import guard，也不装上轮 tracing 探针。
本轮后续组合使用原 hybrid_browser_test.py，不修改导航顺序、断言或重试次数。

首次组合 **0/1，65.271 秒**，错误为 embedding_worker_failed；编码器日志明确为
socket-activate 子进程未获得 PYTHONPATH，不能导入 smartdocs，未运行模型。
这是本轮临时启动配置错误，不是模型计算失败；保留
[失败日志](browser-wiring-failure.txt)。修正为 socket-activate 执行
`/usr/bin/env PYTHONPATH=/absolute/repo/python /path/to/parser-python -B -m smartdocs.embedding_service ...`。
先经真实 Unix socket 发送空数组，得到 embedding_invalid_input，确认服务启动且
未加载模型，再重新运行组合。正式部署手册使用 systemd socket 服务自身的
Environment，不使用本轮临时 socket-activate 包装，因此无需改生产部署环境规则。

修正启动配置后的完整组合仍 **0/1，244.321 秒**，单元退出码 1：

- schema 5 迁移及 103 文件夹具通过；首次编码超时被持久化，唯一显式 API 重试
  后真实向量发布成功，internal 未向量化。
- 页面导航和登录通过，发出了中文查询；结果为 0 条 keyword_only，页面说明
  query_embedding_unavailable / vector_search_unavailable，而非冒称 hybrid。
- 因测试要求真实 hybrid 命中而失败；尚未执行出处、截图、新版和批准撤销断言。
  不把服务子进程退出码 0 当成编码成功，不把这次降级算作混合检索通过。
  查询的具体底层错误未进入现有脱敏日志，不能仅靠约 60 秒服务存活时间断言原因。
- 详见[完整组合记录](browser-combination.txt)。没有本轮搜索截图，也没有读取
  driver.log；M2 未验收，M3–M5 未开始。

本轮 memory.high 从 59471 到 64836（+5365），max/oom/oom_kill 均为 0；
所有本轮临时单元已停止，原 MySQL active，MemAvailable 1890168 KiB，
见[清理终态](unit-status.txt)。临时测试库按原脚本清理，只有人工数据且不保留
恢复副本，未修改原业务库。无远程调用、无新增 swap、无资源限额上调。

下一步不原样重跑：评估固定模型校验/加载的请求生命周期，既要避免每次查询重复
付出全部冷加载成本，也要保留模型完整性、超时回收、非 root 禁网和批准边界。
若方案改变服务生命周期，先明确常驻内存和失效规则，再进行受限验证；本轮未实现
缓存或常驻服务。减少运行时导入已通过兼容性回归，但不代表组合资源问题已解决。
