# 方案A：个人交接报告后端

2026-10-19；基线67270ff，设计2c2c4ae及本轮工作树。
范围是私有草稿保存、实际来源权限校验、JSON读取及Markdown下载，不是M3整体验收。

## 证据

- [初始 RED](red.txt)：旧版没有报告操作 ID，8 项测试失败，无错误。
- [首轮验证](fixture-red.txt)：迁移通过，14 项中11项通过；软删除后用例清理查询404，
  残留队列干扰随后两个用例，最终2失败1错误。已用 finally 恢复文件，保持父类清理有效。
- [首次构建](initial-build.txt)、[最终构建](build.txt)：三个二进制串行构建退出0。
- [独立审查](review.md)：修正Markdown缺少实际来源和任务ID，补充边界测试。
- [源码指纹](source-sha256.txt)：本轮实现、迁移和测试与运行副本逐一cmp一致。
- [最终GREEN](green.txt)：报告HTTP/SQL **14/14**；相关C++ **37/37**，全部通过、无跳过，单元退出0。
  C++分组schema6、auth_project3、m3_17、file_mutation3、authorization2、http_response6；
  最后一组含5项响应单测及1项上传完成响应重放集成测试，不是全仓全量复验。
- [兼容回归](regression.txt)：清单上下文 **8/8**、模型来源台账 **8/8**、清单API **8/8**、
  MCP **46/46**、Python **106/106**，均通过、无跳过，单元退出0。
- [资源终态](resources.txt)：所有本轮测试单元MainPID0，业务MySQL仍active/PID975；
  最终可用内存1437MiB，memory.events与基线一致，无新增节流或OOM。

## 复现环境与命令

当前ECS，副本`/tmp/smartdocs-safe.h4SzvX/repo`，不调用真实模型、不新增依赖、不迁移生产库。
所有重负载串行放`smartdocsvectorcheck.slice`；User=nobody、Group=nogroup、Nice10、UMask0077、
NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
CPU100%、MemoryHigh1200MiB/Max1600MiB/Swap0、TasksMax256；/dev/vda双向5,000,000B/s和200IOPS。
MemAvailable低于512MiB停止自有单元；memory.events基线high80521、max/oom/oom_kill0。

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
export PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python
export SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python
scripts/test-with-mysql.sh -- /bin/bash -c '
 /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_handover_report_test.py &&
 stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
 stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
 stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
 stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
 stdbuf -oL bin/smartdocs_tests --filter=authorization &&
 stdbuf -oL bin/smartdocs_tests --filter=http_response'
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_handover_context_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_model_sources_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_handover_checklist_test.py
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

每个HTTP类使用独立临时库；wrapper校验新库、重放、部分schema拒绝和103文件迁移。
没有重跑全仓C++、前端浏览器或真实供应商质量评测。报告PDF物理页的端到端用例本轮未覆盖，
引用元数据读取复用既有片段页码；报告文本案例校验固定版本/行定位。
后续仍需MCP写工具、网页报告/清单入口、等待继续编排及完整评测。
