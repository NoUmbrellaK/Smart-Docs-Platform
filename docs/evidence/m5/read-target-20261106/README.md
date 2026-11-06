# 读取目标的最小脱敏诊断（2026-11-06）

基线 `b2a0b655924fd190652fcc940fd936f4f9ab4fee` 加本目录关联变更。
针对 [H05 第四次失败](../report-wording-20261106/README.md)缺少具体读取参数的证据缺口，
本轮仅补诊断，不重跑真实模型、不修改提示词、权限或错误映射，也不宣称已修复历史失败。

## 记录范围

在读取工具派发前记录 status/chunk/range 选择器，以及本次运行此前固定搜索/读取返回中
是否出现过文件、文件与版本组合两个布尔值。仅观察同任务、合法请求关联ID和固定位置的
32位十六进制ID；不递归读取正文中的伪造元数据。具体ID仅存在运行期集合中，不写入诊断。
审计落盘失败仍停止派发；字段不进入模型消息，不引入额外请求或自动重试。

汇总器严格校验字段位置、枚举、布尔值及组合一致性，输出可选 read_targets。
旧轨迹不含该字段时保持缺失，不能解释成“未见过”；见过也不能证明当前授权或历史拒绝根因。
工具无配对结果时 status 为 null。业务验收仍为 not_evaluated。

## 验证记录

- [初始 RED](red.txt)：3项方法、5个断言失败，确认缺少记录/汇总。
- [定向 GREEN](targeted-green.txt)：3/3；[首轮完整回归](initial-full.txt)：142/142、97.363秒。
- 审查发现起始/终态事件误放诊断字段会被忽略；补负向用例的
  [RED](placement-red.txt)出现2个预期断言失败，再将位置校验移到提前返回之前。
- 补充 processing/unavailable 返回、运行间观察隔离和审计失败不派发覆盖后，
  [最终完整回归](final.txt)：142/142（MCP 130 + Search HTTP 12），98.718秒。
  覆盖已见组合、错配版本、未见文件、三类选择器、正文伪造元数据、隐私及旧格式兼容。
- 同一最终进程只读核对原 H05 私有轨迹：仍是 failed/scope_changed、2请求/8129 tokens，
  没有 read_targets；SHA-256仍为 `548aba4c4a8ee0c6a01fb4b8c1371ece95c77fa60f42bc385d2822de46fe066f`。
  未从历史记录推断具体错误ID。

[首轮命令](full-command.txt)、[最终命令](final-command.txt)及两轮启动配置分别保留。
最终[前](final-preflight.txt)/[后](final-postflight.txt)指纹一致，25个源文件/测试与运行副本逐字节匹配。
最终进程日志有 OK、兼容检查 PASS 和 Deactivated successfully；
结束后的 transient unit 已回收，[状态快照](final-unit-state.txt)不是运行期配置证明，
实际运行配置见 [final-launch-state](final-launch-state.txt)。

当前服务器串行执行，Python 3.10.12、MCP 1.30.0、httpx 0.28.1；
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、10分钟上限，只允许环回网络。
沿用受限 slice：CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、Tasks256，
磁盘读写各5MB/s、200IOPS。[资源终态](resources.txt)记录 slice 进程数0，
memory.events high82471、max/oom/oom_kill均0，与本轮前基线一致。
内存可用终态1589024KiB；没有新增内存节流或OOM，不代表压力测试成绩。

本轮没有读取API key或调用外部模型；测试模型和后端为本地人工夹具。
未更新固定业务台账、历史引用或人工判定。下一增量可用新增诊断进行受控H05验证；
报告表述修复、真人语义评审、实际归档确认和完整交付仍待完成，M5/P0保持未完成。
