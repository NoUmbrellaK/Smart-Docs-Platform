# H01：全部已保存报告的离线评审材料

2026-11-05，代码基线 `5c113bf95bea14b4e049a60c7d7fc78675764497`。
本增量仅整理证据，不改产品代码、不访问密钥或模型、不确认归档、不改原报告或评审表。
[清单与哈希](manifest.json)绑定三份原始快照、空白评审表和私有阅读副本。

## 覆盖与边界

| 尝试 | 执行状态 | 已保存报告引用数 | 复核重点（AI辅助提示，非人工结论） |
| --- | --- | ---: | --- |
| 1–4 | failed | 0 | 批量上限、预算耗尽、引用格式拒绝；保留各轮证据 |
| 5 | completed | 5 | 问题清单实际存在却被标为未找到，遗漏备份演练待办 |
| 6 | failed | 0 | 输出上限导致model_invalid，没有报告 |
| 7 | completed | 6 | 摘要5项/4必需与实际6项/5必需不一致；“未执行任何写入”与草稿保存矛盾 |
| 8 | completed | 6 | 未解析资料发现能力的笼统限制、部署结论的跨条目来源支撑 |

全部8次尝试仍在原[台账](../fixed-task-results.json)，不只选择最后一次成功输出。
三份完整报告含18个清单项，缺失/无引用项也保留；17是逐次引用出现数（5+6+6），不是唯一来源数。
每次引用均有原始JSON位置、完整关联声明、引用及当次采集的固定版本原文。
完整报告同时保留summary、limitations、recommendations、清单修订、选择范围和来源依赖。
资料里的42项测试/40通过/2失败是模拟项目事实，不是本平台实际测试成绩。

人工已评0条，业务判定与准确率仍为null；没有把哈希一致或可定位当作语义正确。
仍不足需求要求的至少50条，后续需继续固定任务，必要时先冻结补充任务；
30项分母及26项模型任务未执行不变。H01历史36次请求/220188 token，本增量增加0。

## 私有材料与操作

阅读副本：`/tmp/smartdocs-m5-h01-review.GV06RG/review-packet.md`（root，目录0700/文件0600）。
包含原报告、逐引用原文及空白评审字段，只用纯文本查看，不执行资料中的指令、代码或链接。
原始证据及空白评审表保持原位置、原权限（nobody，目录0700/文件0600）：

- 第5次：`/tmp/smartdocs-m5-h01-feedback.Kydw8j/H01`
- 第7次：`/tmp/smartdocs-m5-h01-report-budget.e1Oirr/H01`
- 第8次：`/tmp/smartdocs-m5-h01-report-facts.JcCy9W/H01`

各目录内 `initial/snapshot.json` 为原件、`review.json` 为既有空白评审表。
人工按[评审规则](../../m3/benchmark-review/README.md)逐行填写
`citations[].assessment` 的located、supported、error、notes，以及business的verdict、reviewer、notes。
不得修改来源、结论、行顺序或冻结判据，不重跑prepare覆盖旧表；不在Markdown副本内填写后当作正式评审。
还须阅读全文判断未引用结论、缺失项、限制和建议，不能仅凭17行引用判断业务完成。

只读统计示例（在同版本、nobody可访问的测试副本中，操作者需有切换身份权限）：

```sh
runuser -u nobody -- python3 -B /tmp/smartdocs-safe.h4SzvX/repo/test/e2e/benchmark_review.py summarize \
  /tmp/smartdocs-m5-h01-report-facts.JcCy9W/H01/initial/snapshot.json \
  /tmp/smartdocs-m5-h01-report-facts.JcCy9W/H01/review.json
```

另两次使用对应目录同样统计。权限不足应由受权操作者处理，不放宽原件权限。
人工填写后评审表哈希会变化：保留本次空白基线及新增评审版本/评审者/时间，不能覆盖历史证据来消除失败。

这些路径均为服务器临时目录，重启或清理可能失效，**不是持久备份**。
正式评审前应由受权操作者把整个私有证据集合转存到受控持久存储并核验哈希；
Git仅保存此入口、状态和哈希，不能从公开仓库还原正文。不要公开提交私有副本、快照或评审表。

## 验证与下一步

[只读核验](verification.txt)逐块还原阅读副本中的结构化内容，与三份原件/评审表严格比较，
检查18项、17个位置及原文匹配、不丢失历史失败、原件哈希不变、空白评审状态与台账分母。
此轮是材料整理，不重新宣称产品测试通过，不重跑完整构建/数据库/模型。
继续其余固定任务和M5验收；人工正式评审完成前，不报告业务通过率或引用准确率。
