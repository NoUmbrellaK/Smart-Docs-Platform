# M3 个人交接报告后端

本增量提供持久化和下载，不等于模型报告质量验收。三个 MCP 写工具、网页入口和自动报告编排仍待接入。
每个绑定清单的任务最多保存一份不可变 version=1 的个人草稿；修改内容需新建任务。
Reader 可保存本人报告；项目管理员也不能读取其他人的报告。没有共享或发布功能。

## 接口

前缀 `/api/v1/projects/{project_id}/agent-tasks/{task_id}`，所有接口要求登录、项目成员资格和本人任务。

- `POST /run/start`：绑定清单的新任务额外返回服务端生成的 `report_operation_id`，由可信调用方保留。
- `PUT /handover-report`：同源、JSON 最多 65536 字节，保存后任务原子变为 completed。
- `GET /handover-report`：返回草稿内容、清单快照、所选范围和实际准入来源版本。
- `GET /handover-report/markdown`：受保护的 Markdown 附件，UTF-8、no-store、nosniff。
- `GET /run/result`：报告完成任务返回 `kind=handover_report`、`result=null` 及 `handover_report` 回执。

PUT 请求严格形状如下；不接受额外授权字段、模型 URL 或客户端依赖全集：

```json
{
  "operation_id": "run/start 返回的32位操作ID",
  "report": {
    "summary": "检查摘要",
    "limitations": "检索、解析、分页和覆盖范围限制",
    "recommendations": "交接建议",
    "items": [{
      "key": "清单中既有key",
      "status": "found",
      "notes": "证据与判断说明",
      "citations": [{"file_id":"固定文件ID","version_id":"固定版本ID","chunk_id":"固定片段ID"}]
    }]
  }
}
```

summary/limitations/recommendations 非空，分别最多 8000/4000/4000 UTF-8 字节；notes 非空最多 2000 字节。
每个清单 key 必须恰好一次，不能遗漏或新增。status 为 found/missing/uncertain：found 至少一个引用，
missing 不得带引用；uncertain 可带引用。每项最多 10 个、总计最多 100 个引用，同项不能重复。
引用必须确实出现在已准入模型调用的工具回执中，并仍对应数据库中的同一文件/版本/片段。
服务端补充文件名、正文、物理页（非 PDF 可为 null）、起止行、受保护的固定版本来源 URL。

成功只返回 `{id,version:1,url,markdown_url}`，不把完整报告再塞入模型上下文。
首次保存要求 running、未过 180 秒运行期限、至少一次模型调用准入、正确操作 ID、有效清单及全部任务范围。
准入次数不证明供应商实际收到请求，也不证明模型生成质量。
保存、`handover.report.save` 审计、任务完成在同一事务内；任一步失败全部回滚。
同操作 ID 且相同 JSON 内容重试返回原回执，包括响应丢失、重启及完成后期限已过；不同内容返回 409。
取消或超时后不能首次保存。重试仍重验当前权限与来源，不绕过撤销。

## 依赖与输出安全

完整依赖来自 C++ 的 `agent_tool_calls.source_json` 中 `model_call IS NOT NULL` 的并集，
包括模型见过但没有引用的资料/元数据，不以模型提供的引用清单充当全集。
保存后的读取、下载、幂等重试和运行结果均重验该实际依赖的当前版本、软删除、外发批准及清单修订。
仅选择但从未准入的文件不伪装成实际来源；其后续策略变化不会阻断已保存报告。
成员或本人资格不满足返回 404；依赖失效返回 409，均不返回旧报告正文。

JSON 中正文是非可信数据，客户端必须按纯文本渲染。Markdown 对 HTML 和 Markdown 控制字符转义；
链接由后端固定来源路径生成，不使用模型给出的跳转地址。`verification=citations_validated_not_semantically_reviewed`
明确表示只验证出处与结构，未验证摘要/缺项判断语义，不应把草稿当作已人工验收成果。

## schema14和部署

`014_handover_reports.sql` 增加运行操作 ID 和 handover_reports 表；唯一键约束每任务/操作最多一份报告，
复合外键绑定运行与操作，版本固定为 1。旧运行的操作 ID 保留 NULL，不补造报告授权。
停止应用、worker 和网关，备份数据库及资料，再运行 `scripts/migrate.sh`，成功输出 `schema_version=14`。
新二进制要求 schema14；不能直接启动于 schema13 或更早数据库。DDL 非整批事务，失败恢复匹配备份，
不要盲目重放。同步发布二进制及受信调用方；本轮只在隔离库验证，不迁移生产。

证据见[报告后端验证](../evidence/m3/handover-report/README.md)。
