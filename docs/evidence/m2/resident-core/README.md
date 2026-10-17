# M2 已校验内容的常驻编码核心

基线 `5bf1073296696e8e07eefc1c65cf79e9330ecf95` 的完整 SHA 校验不使用文件元数据缓存。
本轮新增 `ResidentEncoder` 核心组件，尚未接入生产 socket 服务，不是 M2 验收。

## 生命周期与完整性

仅使用标准库和现有模型依赖。所有五个模型文件按固定 SHA 校验：边读边复制到
Linux memfd（每文件上限 256 MiB），校验通过后增加 WRITE/GROW/SHRINK/SEAL
封印。全部文件通过后才允许原生库读取封存 fd。加载完成关闭快照；模型和分词器
保留在进程内。源路径后续变化不影响该实例；新实例必须重新验证全部文件。
这不是声称源文件不会变化，也不是以 mtime/ctime 代替内容校验。

ONNX Runtime 的 bytes 入口会保留原始模型 bytes；因此改用封存 fd 路径，加载后
关闭权重快照并禁用 provider fallback，不允许以后重建 session 或设置 provider。
当前固定 CPU provider/模型不含外部权重；更换模型必须重新验证此边界。
与已有分阶段实现共享窗口、推理和归一化函数，不改变索引修订或输出格式。
旧 `encode_chunks` 入口仍逐请求完整校验、持有原锁和 60 秒截止期。

调用方必须非 root、串行、禁网并设置 cgroup 限额。核心本身不是鉴权入口，也
尚无 socket、请求 watchdog 或超时后杀死/重启协议；这些是接入生产前的门槛。
将来仍须保留 C++ 编码前后鉴权及具体版本批准检查，不得用预热替代权限检查。

## 验证方式

先以缺少核心模块的真实受限测试得到失败，见 [initial-red.txt](initial-red.txt)。
轻量测试覆盖源内容改写后的快照独立性、写入/截断被内核拒绝、关闭后 fd 失效、
错误哈希、符号链接、FIFO、目录、超大文件拒绝以及失败路径不泄露描述符。
真实测试使用临时硬链接，加载后只删除这些夹具链接，不修改原模型内容。
旧版六样例完整结果指纹为
`6b38c9123a3a3050482b1ea2199b4f83db6475dcfafea3289d429473f0953dd8`，
不是由新实现生成期望值。连续五次调用都必须一致，且新实例拒绝缺失源文件。

串行运行在原 `smartdocsvectorcheck.slice`：MemoryHigh=1200 MiB、Max=1600 MiB、
Swap=0、CPU=100%、Tasks=256、读写各 5 MB/s/200 IOPS、Nice=10。
模型单元 nobody/nogroup，AF_UNIX、PrivateNetwork、NoNewPrivileges、240 秒总寿命。
测试进程虚拟内存 2 GiB、CPU 总计 60 秒，启动期文件上限 256 MiB 用于 memfd；
快照关闭后收紧到原 2 MiB。这个启动期差异只用于新核心实验，未放宽旧服务或
请求输出上限。继承更严限制时不放宽。复现入口：

```bash
PYTHONPATH=python /path/to/model-python -B test/e2e/m2_resident_embeddings_test.py /path/to/model
PYTHONPATH=python /path/to/parser-python -B -m unittest discover -s test/python -v
/path/to/model-python -B test/e2e/m2_embeddings_test.py /path/to/model /path/to/model-python
```

以上命令必须置于上述受限单元，模型测试不并行；不清空系统页缓存，不调用远程 API。
首次真实运行 **1/1、6 项检查通过**，见 [real-model.txt](real-model.txt)：初始化
35.810 秒，五次请求为 0.570/0.608/0.558/0.559/0.564 秒，旧完整向量指纹一致。
请求结束 RSS 为 632400 KiB，进程峰值 636924 KiB（约 622 MiB）。这是小夹具
复用验证，不是浏览器组合、准确率评测或 SLA；页缓存状态未受控。
常驻 RSS 高于上一轮分阶段子进程峰值约 461 MiB，不能只凭请求更快判断方案可上线。

完整 Python 回归 **86/86、14.183 秒**，见 [python-regression.txt](python-regression.txt)。
旧正式分阶段入口 **1/1、11 项检查通过**，见 [legacy-model.txt](legacy-model.txt)：
完整向量指纹仍一致，首次/再次编码 16.664/3.257 秒，子进程峰值 472348 KiB。
本轮未修改 C++ 或前端，未重跑它们的全量套件或浏览器组合。

独立只读审查无 Critical/Important；按建议补充构造后无快照/源文件 fd 的直接断言，
最终真实复验 **1/1、7 项检查通过**，见 [real-model-final.txt](real-model-final.txt)。
复验初始化 2.109 秒，五次请求 0.555–0.575 秒，峰值 637448 KiB（约 623 MiB）；
前一次测试已经读取模型，不将这次初始化解释为冷启动速度，也不累计重复测试分母。
逐个破坏五种模型文件的完整构造负例尚未覆盖；通用 SHA 拒绝
有轻量测试，五文件先校验后加载由实现和静态审查确认，不冒称已逐文件动态验证。
下一增量必须先明确启动就绪/独立启动预算、单请求截止期、取消或超时后进程回收与
串行服务协议，再接入 socket 并做原配额下浏览器/数据库组合。当前服务入口不变。

## 资源终态

本轮 memory.high 从 64836 到 64879（+43），max/oom/oom_kill 均为 0。
全部本轮测试单元 inactive/dead、退出码 0；原 MySQL active，MemAvailable
1907408 KiB，见 [unit-status.txt](unit-status.txt)。没有安装常驻生产进程、迁移
业务库、调用 DeepSeek 或提高共享资源配额。临时模型夹具链接已由测试移除，
原模型保留。源码与执行副本逐文件 cmp 一致，指纹见 [source-sha256.txt](source-sha256.txt)。
