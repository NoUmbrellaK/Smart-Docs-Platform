# 方案A：报告MCP与执行器

2026-10-19；基线f525e04及本轮工作树。实现第三个工具save_handover_report，
绑定清单任务通过真实工具保存个人报告；没有实现两个归档MCP工具或宣称M3验收完成。

## 证据

- [初始RED](red.txt)：7项新行为在旧实现下全部失败，无测试错误。
  更早调试运行的断言被SDK异常组包装，已移到上下文外；此处记录规范化后的失败结果。
- [初版单测](unit.txt)：53/53；[初版真实链路](initial-e2e.txt)：4/4。
- [独立审查](review.md)发现原生产512输出token上限不适合完整多项报告；
  [预算RED](budget-red.txt)复现后修正，保持普通问答的省token上限。
- [最终单测](final-unit.txt)：57/57，无失败/跳过。涵盖实际适配器请求预算、错误操作ID、
  延迟提交、未知结果、丢失回执、不混批和普通问答兼容；供应商请求由本地HTTPX接收器替代。
- [源码指纹](source-sha256.txt)：实现与测试均和运行副本逐一cmp一致。
- [最终真实链路和回归](green.txt)：报告SDK/stdio/C++/SQL **4/4**、清单上下文 **8/8**、
  来源台账 **8/8**、既有Python **106/106**，无失败或跳过，单元退出0。
- [资源终态](resources.txt)：本轮全部单元MainPID0，最终测试退出0；原MySQL仍active/PID975。
  memory.events与基线一致，无新增节流/OOM。RED退出1为预期失败，未清除历史记录。

## 复现

当前ECS、既有隔离副本`/tmp/smartdocs-safe.h4SzvX/repo`，无新依赖、远程模型、schema变更或生产迁移。
C++未改动，复用上一增量已验证的schema14二进制，本轮不重复构建或全量C++测试。
重负载串行使用smartdocsvectorcheck.slice：CPU100%、MemoryHigh1200MiB/Max1600MiB/Swap0、
TasksMax256；/dev/vda双向5,000,000B/s和200IOPS；User=nobody、Group=nogroup、Nice10、
UMask0077、NoNewPrivileges、RuntimeMaxSec300/600/900、TimeoutStopSec5、KillMode=control-group。
MemAvailable<512MiB停止自有单元。memory.events基线high80521、max/oom/oom_kill0。

```bash
export PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python
export SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_report_mcp_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_handover_context_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_model_sources_test.py
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

HTTP各类独立临时库，包含迁移、重放、部分schema拒绝和103文件迁移检查。
报告用例通过本地脚本化决策产生真实SDK/HTTP调用，并另以独立stdio进程实际保存。
不是供应商效果/引用语义评测，也未验证网页、生产内部HTTP或任意50项长报告的实际模型成功率。
工具/消息字节、输出额度或截止期不够时仍可能失败，不能因此静默删项或宣称齐全。
