# 否决基于文件元数据复用模型校验

2026-10-18，基线 `6f17e542d51d735fb38c876dc5090773e2a2896b`。
本轮验证一个减少每次编码 SHA 读取的候选：管理员先完整校验固定模型并生成
root 所有的只读凭据，后续只在受保护路径和 dev/inode/size/mtime_ns/ctime_ns
相符时复用。**安全回归失败，方案已否决，生产改动全部撤回。**

## 实际证据

1. 先增加回归，旧实现因没有准备入口失败，见 [initial-red.txt](initial-red.txt)。
2. 候选实现允许完整哈希缺省路径、拒绝不安全所有权/路径，并比对元数据快照。
   但真实 root 准备、nobody 验证的小文件回归在第一个“同长度改写”用例失败：
   应返回 embedding_model_unavailable，实际返回 ok。
   [metadata-failure.txt](metadata-failure.txt)记录写入前后 mtime_ns、ctime_ns
   均为 `1792258897811445096`。后面的变更用例未执行，不声称它们已通过。
3. 用不依赖候选代码的 [same_tick_probe.py](same_tick_probe.py) 再次验证：
   非 root、禁网、几字节临时文件，第一次即出现同样碰撞。
   dev=64515、inode=147213、size=3，写入前后两种时间均为
   `1792258952315939370`，但实际读取内容的 SHA 从
   `ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad`
   变为 `2f05d4b689d270cafb02285f35f44866f7dc8a2d368a3f9d1124373eeab31fb1`。
   见 [独立实验日志](probe-result.txt)及从 journal 提取的 [UID=65534 证据](probe-identity.json)。
   字段以 ns 表示不保证每次写入都取得新值。

这足以否决本候选的“元数据相同即可证明字节未变”假设；不是 SHA256 碰撞，
也没有证明模型计算出错或服务器发生 OOM。候选回归由 root 改写夹具，并非证明
普通用户能篡改 root 只读模型；独立探针使用 nobody 自己的文件。独立探针只记录首次观测，不把一次
碰撞的出现概率泛化成全部文件系统指标。即使探针某次未复现，也不证明方法安全。

## 环境事实与恢复

只读 namei/stat 还确认旧测试模型目录和五个文件都属于 nobody，目录 0755，文件
0644；其上 vector-probe 为 nobody/0700，smartdocs-safe.h4SzvX 为 nobody/0755。
它们是现有隔离测试资产，不是按生产手册部署的 root 管理模型。本轮未改变其
所有权，也未创建真实模型凭据；不能以生产手册约定代替测试环境的实际状态。
后续涉及跨请求复用时必须明确并验证模型加载与更新边界。

候选差异归档为 [rejected-candidate.patch](rejected-candidate.patch)，对应失败测试
为 [rejected-test.py](rejected-test.py)，仅供研究，**不得接入运行环境**。
若需复现候选，应在基线的独立一次性副本应用 patch，并将 rejected-test.py
放至 test/e2e/m2_model_receipt_test.py；测试父进程需要 root，仅创建几字节夹具，
验证子进程降为 nobody。它不是默认回归套件的一部分，预期安全断言失败。
更推荐直接以普通账号运行独立探针，无需修改生产代码：

```bash
python3 -B docs/evidence/m2/rejected-metadata-cache/same_tick_probe.py
```

生产 embeddings.py 及执行副本均恢复为基线 SHA：
`98942bdc12dc7c74fcf3a9c9564f297482d152e2c179a4865a0bc90c8a372df5`。
删除了仅本轮创建的 prepare_model.py 和候选测试入口（包括执行副本），
候选核心和失败测试可由归档恢复，未删除用户数据。临时夹具由脚本清理。
本轮只做轻量文件系统检查，未加载真实模型、未跑浏览器、未调用 DeepSeek，
未重跑未改动的完整回归；不引用上轮绿色结果作为本轮新测试成绩。
清理后无运行测试单元，memory.high 仍为 64836，oom/oom_kill 均为 0，
原 MySQL active，见[终态](final-status.txt)。独立审查同意否决结论；
其要求补充的独立探针原始输出和执行身份已归档。

## 下一步

不再尝试仅凭时间戳缓存完整性结果。下一增量验证“从实际校验过的内容构造一个
受限常驻编码实例”，在复用已加载内容的同时明确重启/更新语义、内存上限、
取消和超时回收；不能在读取被改写文件时直接沿用旧校验结果。
该方向尚未实现或验证，不宣称能解决组合超时。M2 仍未验收，M3–M5 未开始。
