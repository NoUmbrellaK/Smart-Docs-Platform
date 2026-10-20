# M5 systemd 离线配置校验

2026-10-21，基线 `b51af45` 加[校验入口](../../../../test/e2e/m5_systemd_units_test.py)。
只向临时目录写入文档中的 unit，调用本机 systemd 249 的
`systemd-analyze --man=no --generators=no verify`；不安装 unit、不 reload、不启动服务，
不读取凭据、不调用模型、不修改生产数据库。

## 结果与边界

两套配置均完成校验：逐请求 socket 模式 **8 个 unit**、常驻模式 **7 个 unit**。
这些是互斥配置的文件数量，不是 15 个已部署服务。
原样 `/opt` 配置均因可执行文件尚未部署而退出 1；
仅将三个 ExecStart 可执行文件路径替换为已有测试文件后，两套均退出 0 且无项目诊断。
参数、环境变量、工作目录和其他运行时路径未替换。

每套各做两个负向对照：缺失依赖返回非零；未知项目选项虽然返回 0，
仍能从诊断中检出。因此成功条件不只检查退出码。
最终共两套正向配置、四项负向对照，不计入固定 30 项业务任务。
[完整结果及合成 unit 指纹](verified.txt)、[源码指纹和主机终态](sources-status.txt)。

首次校验入口错误地要求所有诊断为空，遇到宿主已有 unit 警告而失败，
即使替换路径后的校验已返回 0。[首次输出](initial.txt)完整保留。
随后仅将带系统 unit 路径的诊断单独识别，保留原始全文，
临时项目 unit 和未识别诊断仍使正向校验失败。最终重验通过；
共一次失败、一次成功，不是两次产品部署尝试。

宿主警告包括 snapd 的未知 RestartMode、mihomo unit 可执行权限、
cloudmonitor 的 KillMode=none 和旧 PIDFile 路径。本轮未修改这些系统服务，
也不宣称宿主整体配置健康。依赖解析使用默认宿主 unit 搜索路径。

按 [systemd 官方说明](https://www.freedesktop.org/software/systemd/man/247/systemd-analyze.html)，
verify 可检查 unit 配置、依赖和可执行文件，但不会执行服务。
本轮不验证 Python 模块能否导入、环境文件、模型、账号权限、运行时 socket、
实际沙箱效果、就绪状态或全组件启动。`/opt` 原路径缺失仍是实际部署待办；
不得把替换路径后的通过当作可直接上线。

## 文档组装与复现

入口直接提取 M1、解析 worker、向量 worker、搜索及 Agent 手册中的 ini 块，
按照向量手册将两个 worker 放入共享 slice，并将 vector 差异配置应用到 parser 模板。
常驻模式仅切换 vector/search 的编码服务依赖与 socket 路径；
Agent 的可选 encoder 仍为注释，未作为启用后的配置验证。
没有修改部署模板或产品代码。

```sh
nice -n 10 python3 -B test/e2e/m5_systemd_units_test.py \
  --server /tmp/smartdocs-safe.h4SzvX/repo/bin/server \
  --parser-python /tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  --mcp-python /tmp/smartdocs-mcp.P7grAI/venv/bin/python
```

上述路径是当次已有测试可执行文件，复现时须提供本机存在的绝对路径；
脚本不会执行它们，也不会自动安装缺失依赖。临时 unit 随调用结束清理。
终态共享测试 slice TasksCurrent=0，原 MySQL active，可用内存 2018MiB。
这些抽样不是峰值测量或长期稳定性证明。独立只读复核未发现阻断项；
M5/P0 仍未完成，整套部署、真实模型业务评测与真人确认演示仍待完成。
