# 方案A：可信任务清单上下文

2026-10-19；业务基线55b3bee，设计167f5fc及本轮工作树。
本增量完成清单到任务/执行器链路；不是报告保存或五工具整体交付。

## 证据

- [初始RED](red.txt)：schema12下4项HTTP用例（含子用例8个失败断言）和1项Python用例失败；
  原创建接口拒绝新修订字段，原执行器忽略畸形清单仍调用模型。
- [边界RED](edge-red.txt)：初版schema13下7项HTTP，5通过、2失败：缺确认仍200、超大start仍200。
  已按[审查](review.md)修正；新增归档回执例外测试，最终HTTP共8项。
- [初版构建](initial-build.txt)与[修正后构建](build.txt)均成功退出0，三个二进制串行构建。
- [GREEN](green.txt)：清单上下文HTTP/SQL/真实SDK链路 **8/8**、相关C++ **31/31**，无失败或跳过。
  C++分组schema6、auth_project3、m3_17、file_mutation3、authorization2；不是全仓C++全量复验。
- [源码指纹](source-sha256.txt)：所有本轮变更的实现/测试与运行副本逐一cmp相同。
- [兼容回归](regression.txt)：既有清单API **8/8**、来源台账 **8/8**、MCP/执行器单测 **46/46**、
  既有Python **106/106**，均通过、无跳过，单元退出0。
- [资源终态](resources.txt)：全部本轮单元MainPID0，RED保留退出1，最终build/green/regression退出0。
  原MySQL仍active/PID975；memory.events与基线一致，无新增节流/OOM；最终可用内存1458MiB。

## 复现

当前ECS，副本/tmp/smartdocs-safe.h4SzvX/repo；无新增依赖/生产迁移/付费模型。
所有重负载串行放smartdocsvectorcheck.slice，User=nobody/Group=nogroup、Nice10、UMask0077、
NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
CPU100%、MemoryHigh1200MiB/Max1600MiB/Swap0、TasksMax256；/dev/vda双向5,000,000B/s和200IOPS。
MemAvailable低于512MiB停止自有单元。基线memory.events high80521、max/oom/oom_kill0。

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
export PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python
export SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python
scripts/test-with-mysql.sh -- /bin/bash -c '
 /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_handover_context_test.py &&
 stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
 stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
 stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
 stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
 stdbuf -oL bin/smartdocs_tests --filter=authorization'
scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_handover_checklist_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_model_sources_test.py
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

每个HTTP类使用独立临时库；wrapper检查新库、重放、部分schema拒绝与103文件迁移。
模型接收器为本地替身，不证明真实供应商效果或收件成功。未复验前端/浏览器或全仓C++。
个人报告保存/Markdown/页面、清单网页选择、目录上下文、三个写工具和生产Agent入口仍未交付。
