# M4 功能门槛复核

2026-10-20，被核对/测试提交 `6a36ffc77a969688675f8c20c2c846501c93052c`。
依据需求A-02、A-03与第14节；不修改产品源码、测试断言、schema、模型提示词或冻结任务集。
按最小实现原则复用已有隔离测试，验证实现和证据是否足以进入下一阶段，不重复建设报告服务。

## 结论

**M4功能阶段门槛满足，可以进入M5；整个P0仍未完成。**
需求第14节的“未确认操作被拦截、冲突可处理、报告有来源”均有当前实现和对应实际证据。
不将阶段关闭扩大成模型分类/报告语义质量、生产上线或T-01至T-20最终整体验收通过。

## 本轮实际验证

[完整日志与资源终态](./journal-and-status.txt)：

- `m3_handover_report_test.py`：**14/14**，26.503秒，无失败/跳过。
  覆盖reader私人保存、固定片段、来源全集（包含未引用资料）、撤销访问、
  新版本不替换旧引用、非法引用/操作/取消、SQL审计故障回滚、并发幂等及丢响应重启。
- `m3_report_browser_test.py`：**1/1**，18.888秒，无失败/跳过，本轮重新执行一次，未追加失败重试。
  真实reader登录查看三类状态和固定来源；实际下载Markdown逐字节等于后端附件；
  撤销未引用但已准入资料后，下载与来源读取均阻断并清空旧正文；
  另一个项目管理员不能读取该私人报告。六宽度无溢出、按钮至少44px。
  报告由人工协议夹具保存，不是模型生成质量测试。

已目视检查本轮[360px](./report-360.png)、[1280px](./report-1280.png)截图：
私人草稿提示、范围与长ID换行、纯文本注入样例正常。截图为滚动后的局部视口，
不代替整份报告状态/来源/下载断言，也不代表完整业务演示。

归档侧独立只读复审没有发现功能阻断；报告侧代码复核：
`handover_reports.cpp`的Sources只取实际准入的工具账本，ValidateReport检查全部清单项
与固定引用；SaveHandoverReport将报告、审计与任务终态同事务保存；
读取和导出通过RequireReportDependencies重新检查清单及来源集合。
网页显示私人草稿、范围、三类状态、限制、建议与固定出处，正文按文本渲染，
来源点击/Markdown下载重新鉴权，失败清屏、不复用旧内容。
这些结构与权限校验不证明真实模型结论受到原文支持。

## M4 条件与已有证据

下表明确区分本轮复验和已有记录；不同套件不合并为业务完成率。

| 要求 | 实现及直接证据 | 结论边界 |
| --- | --- | --- |
| 选择范围、预览逐项文件/版本/位置/理由 | [任务模式](../../m3/task-mode-ui/README.md)、[提案Agent](../../m3/archive-agent/README.md)；当前agent.js与agent_runner.py按显式模式工作 | 独立创建路径和脚本模型协议证据，不是实际分类质量 |
| 拒绝/调整新修订及重新确认，未确认不得移动 | [人工调整](../manual-revision/README.md)：前一增量108/108、8/8、真实浏览器1/1；同方案新修订真实网页PUT | 人工修改不称为模型重新分类；保存、确认、执行是三个独立动作 |
| 确认绑定修订，冲突与整批事务 | [后端边界复验](../boundary-recheck/README.md)：22/22；当前archive_plans.cpp无后续变更 | T-13/14/15功能路径；SQL中途故障不等于整机断电演练 |
| 已提交丢响应、重启及幂等回执 | 同上与[显式继续](../../m3/archive-resume/README.md)；本轮报告HTTP并发/丢响应/重启检查 | 不会重做移动/保存；历史回执不承诺当前文件位置 |
| 管理员维护必需/可选清单、任务绑定批准修订 | [配置网页](../../m3/project-settings-ui/README.md)、[可信上下文](../../m3/handover-context/README.md)、[任务模式](../../m3/task-mode-ui/README.md) | 使用历史真实浏览器/HTTP证据，本轮未重复运行设置浏览器 |
| 报告基本信息、三类状态、限制、建议与固定引用 | 本轮14项HTTP和报告浏览器；[报告MCP复验](../boundary-recheck/README.md)4/4 | 片段存在/授权有效不等于语义准确；缺失/冲突结论质量须后续人工评审 |
| 私人草稿、Markdown、来源全集撤销 | 本轮HTTP与报告浏览器；后端Sources、ReadHandoverReport及网页renderReport/source/downloadReport | 未引用但实际准入的版本仍触发阻断；用户已下载的副本无法远程撤回 |

## 环境、指纹与复现

[副本核对](./preflight.txt)：226个受跟踪代码/脚本/资源中223个相同，
差异仅为副本缺两份依赖清单、未运行的旧M1浏览器自测不同。
当前C++/Python及报告测试与此前`ab620f5`核对基线没有差异，agent.js新增80行人工编辑逻辑。
工作树bin是旧构建，本轮不使用；执行安全副本中此前已验证的server/admin，
两处二进制哈希都明确保留，不声称它们相同。没有重新编译或替换生产进程。

[活动运行配置](./running.txt)：单元`smartdocs-m4-report-gate`；
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges、900秒上限、control-group回收，
IPAddressDeny=any且只允许localhost；CPU100%、内存软/硬1200/1600MiB、Swap0、
磁盘读写各5MB/s和200IOPS不变。停止阈值为MemAvailable低于512MiB。
临时MySQL仅私有socket、32MiB缓冲池；每套用新的独立临时库并验证schema16。
初始化空root密码告警仅属于隔离测试库，不涉及原业务MySQL。

在`/tmp/smartdocs-safe.h4SzvX/repo`上述受限单元中串行执行：

```sh
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_handover_report_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_report_browser_test.py
```

PYTHONPATH为副本python及test/e2e；解析器为安全副本venv；
Chrome/Driver为安全目录chrome-linux64/chrome、chromedriver-linux64/chromedriver，沙箱开启。
截图目录`/tmp/smartdocs-m4-gate.VbSFBf`。不加载DeepSeek环境，不访问远程模型，
不安装新依赖、不运行生产迁移、不开启后台自动重试或提高资源限制。
单元22:07:49—22:11:59 CST，含两套临时库初始化、迁移及清理，不是业务SLA。
memory.events前后high80521、max/oom/oom_kill均0，无新增内存节流/OOM；
启动前MemAvailable2207520KiB、保存的活动采样2063396KiB、结束2222620KiB，
不是连续最低值或峰值RSS。最终MainPID0、TasksCurrent0、原mysql active。
夹具已清理两套临时数据库及人工上传样本，不能直接恢复；没有删除业务资料。

## 不在本轮完成范围内

固定30项真实业务评测、全部实际引用的人工语义评审（不足50条须冻结补充任务）、
真实模型完整业务演示及最终匹配版本部署验证仍未完成。
既有M1中断、MCP矩阵、备份恢复等证据仍需在最终T-01至T-20验收索引中逐项核对，
不能由本轮报告通过推导全项目通过。已有三次限定DeepSeek授权已消费，
本轮不复用该授权，也不将协议夹具记为真实业务结果。

归档检查：205个本地文档链接均存在；30项冻结任务仍为`not_run`；
证据凭据模式扫描无匹配，完整提交差异格式检查通过。这些检查不增加产品测试分母。
