# 方案A：模型来源回执链路

2026-10-19；业务基线bb67fbd，设计f52b1e8及本轮工作树。
本增量不是个人报告/MCP写工具交付；报告后续须消费本账本，不能只采信模型引用。

## 验证记录

- [首次RED](first-red.txt)：协议缺失失败后夹具遗留运行任务，后续用例被运行槽429拦截。
  修正为每个用例结束取消本测试项目未终结任务；未改变生产并发限额。
- [RED复验](red.txt)：6项HTTP/SQL及3项Python协议检查均因旧实现缺失来源记录/回执协议失败。
  旧check无body被接受、新body被拒绝；缺回执仍调用模型8次；Hybrid没有最终后端回执。
- 新增覆盖：只读成员对processing/unavailable元数据的准入、直接数据库约束、来源正文伪造字段。
- [构建](build.txt)：server、smartdocs_tests、smartdocs-admin串行构建成功，单元退出0。
- [夹具失败](fixture-restart-failure.txt)：补强后的8项HTTP断言未报告失败，但重启用例清理仍连接旧端口而报错。
  参照已有测试重建客户端并登录后复验，未改变生产行为或资源上限。
- [GREEN](green.txt)：来源HTTP/SQL **8/8**、C++ **32/32**，无失败或跳过。
  C++分组为来源提取1、schema6、auth_project3、m3_17、file_mutation3、authorization2。
  新库、重放、部分schema拒绝、103文件升级检查通过；旧数据未伪造来源。
- [静态审查](review.md)及[源码SHA256](source-sha256.txt)已记录；所有列出的生产/测试文件与副本逐一cmp一致。
- [回归](regression.txt)：真实MCP链路 **5/5**、工具审计 **4/4**、MCP/执行器单测 **45/45**、
  既有Python **106/106**，均无失败或跳过，单元退出0。
- [最终资源](resources.txt)：全部自有构建/验证单元MainPID=0；原MySQL仍active/PID975。
  memory.events high始终80521，max/oom/oom_kill始终0，无新增节流/OOM；末次可用内存1468MiB。
  失败单元保留failed退出1，未隐藏或计作通过；修正后的green与regression退出0。

## 复现与资源边界

当前ECS，/tmp/smartdocs-safe.h4SzvX/repo副本，无依赖安装、生产迁移、付费模型或编码器加载。
MCP解释器/tmp/smartdocs-mcp.P7grAI/venv/bin/python；解析器/tmp/smartdocs-safe.h4SzvX/venv/bin/python。
每个测试单元使用User=nobody/Group=nogroup，Nice10、UMask0077、NoNewPrivileges、RuntimeMaxSec900、
TimeoutStopSec5、KillMode=control-group。重负载串行放smartdocsvectorcheck.slice：CPU100%、
MemoryHigh1200MiB/Max1600MiB、Swap0、TasksMax256；/dev/vda双向5,000,000B/s和200IOPS。
MemAvailable低于512MiB停止自有单元；不变更业务MySQL。

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
make -j1 bin/smartdocs_tests # 补强来源单测后的增量构建
stdbuf -oL bin/smartdocs_tests --filter=agent_sources
export PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python
export SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python
scripts/test-with-mysql.sh -- /bin/bash -c '
 /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_model_sources_test.py &&
 stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
 stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
 stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
 stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
 stdbuf -oL bin/smartdocs_tests --filter=authorization'
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_tool_audit_test.py
/tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -p 'test_*.py' -v
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p 'test_*.py' -v
```

不同HTTP测试类使用新临时库，wrapper检查新库、迁移重放、部分schema拒绝及103文件升级。
真实SDK执行器用例的模型是本地接收器，验证来源在调用前持久化，不证明真实供应商质量或收到请求。
账本保守保留已准入但可能未送达的来源；没有把“准入”表述为模型成功。

## 剩余边界

只记录文件工具来源，清单/目录上下文与报告绑定尚未实现。普通回答草稿仍按全任务范围重验。
报告保存、引用归属/语义验收、展示/Markdown、5工具及生产Agent入口仍待完成。
未复验前端、浏览器或全仓C++，不将分组成绩扩大为整体P0验收。
