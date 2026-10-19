# 方案A：项目交接清单基础

2026-10-19；业务基线ee7b917，设计记录8da5d06，加本轮变更。
本增量是报告的项目元数据前置条件，不代表个人报告、MCP写工具或M3整体交付。

## 范围

普通项目业务接口GET/PUT handover-checklist，当前成员读、管理员同源写。
支持稳定key、标题、描述、必需/可选、顺序、修订及明确外发批准；不自动生成模板或批准。
新请求明确设置批准值，false清除批准；旧修订冲突，同一最近请求重放不重复审计。
成员→项目行锁串行化同项目写入，清单及审计同事务，失败整批回滚。
schema11仅新增清单表，不回填报告或权限。新旧二进制须与各自schema版本匹配。

## 实际验证

- [RED](red.txt)：既有权限基线1通过、1因无测试库跳过；新7项HTTP用例未实现时全部失败，
  包含子用例共33处失败，都是缺少路由404。未使用mock替代数据库或业务服务。
- [构建](build.txt)：server、smartdocs_tests、smartdocs-admin受限make -j1退出0。
- [首轮](first-green-failed.txt)：8项HTTP中7通过、1夹具错误；CREATE TRIGGER复合语句缺少mysql
  客户端DELIMITER，尚未进入回滚断言。参照既有归档夹具补分隔符，未修改业务代码或减少断言。
- [复验](verified.txt)：先用[原SQL复现脚本](trigger-repro.py)确认1064，再运行HTTP **8/8**、
  C++ **31/31**（schema6、auth_project3、m3_17、file_mutation3、authorization2），无失败/跳过，单元退出0。
  覆盖默认值、重启、精确重放/审计计数、撤销批准、旧请求冲突、角色降权/越权/同源、输入边界、
  不同请求竞争、相同请求竞争、审计故障回滚，以及直接SQL验证新表JSON/批准/修订/外键约束。
- [独立临时库归档回归](archive-regression.txt) **22/22**，单元退出0；包括原归档策略、预览、
  真实确认、整批回滚、并发执行和丢失响应恢复。这不是全仓C++/Python/浏览器完整复验。
- [只读审查及复查](review.md)的三项问题已修正，夹具修复另经复查。[源码指纹](sources.sha256)
  对应实际受限副本，逐文件cmp一致；文档不影响测试二进制。
- 故障注入在临时MySQL审计INSERT触发器中SIGNAL，仅用于验证同事务回滚，不是磁盘断电演练。

## 运行环境与复现

仍用当前ECS及已有副本/tmp/smartdocs-safe.h4SzvX/repo；不安装依赖、不调用DeepSeek。
重负载串行置于smartdocsvectorcheck.slice，CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、
TasksMax256；/dev/vda双向5,000,000B/s与200IOPS。User=nobody、Group=nogroup、Nice10、
UMask0077、NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
MemAvailable低于512MiB时停止本轮自有单元。生产MySQL不在测试范围。

构建：`make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin`。
设置PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python，在受限单元内执行：

```bash
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /bin/bash -c '
 /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_handover_checklist_test.py &&
 stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
 stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
 stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
 stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
 stdbuf -oL bin/smartdocs_tests --filter=authorization'
/tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_archive_execution_test.py
```

两个HTTP测试类使用各自临时库，避免复用固定夹具账号。wrapper检验新库、迁移重放、部分schema
拒绝和103文件升级，并在退出时清理skip-networking数据库。没有生产迁移/服务发布。

复验前的诊断命令为 `python -B /tmp/smartdocs-checklist-trigger-repro.py`，内容归档在trigger-repro.py；
该脚本只接受wrapper生成的/tmp/smartdocs-mysql.* socket。预期1064是失败夹具的诊断证据，不是业务测试失败。
最终资源状态见[resources.txt](resources.txt)：本轮切片high保持80521，max/oom/oom_kill均0；
重负载单元已终止，无临时服务遗留。没有扩大资源限额、安装依赖或调用DeepSeek。

## 下一步

补项目设置交互、清单版本绑定、报告保存/读取/Markdown及可信出口来源依赖，再接第五工具与
其他写工具的受控编排。普通成员可读的清单不自动成为可外发模型上下文；当前未接入模型消费。
不以元数据API代替需求中的交接核查、引用质量或完整演示验收。
