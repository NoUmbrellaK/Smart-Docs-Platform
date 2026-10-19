# 方案A：目标授权与持久归档预览验证

2026-10-19；基线 `04aa434e2f6c92bf25f14644b4bb28ca127f239b` 加本次变更，
最终被测源码见[source-sha256.txt](source-sha256.txt)。逐文件cmp确认工作树与受限副本一致。
用户已批准方案A。本增量不代表M3/M4或P0整体验收，也没有执行归档移动。

## 实现和结果

- 管理员同项目目标白名单与元数据外发批准；修订比较和重放；目录改名撤销批准。
- 本人任务持久预览、稳定计划ID、显式修订；待确认目标为null，始终executable=false。
- 全任务范围及当前版本/权限/外发/位置重验，目标批准和MySQL排序规则同名冲突校验；
  不移动文件、不产生移动确认、不接MCP写工具。
- 真实HTTP **9/9**；受影响的C++回归 **31/31**、0失败、0跳过：M3 17、schema 6、
  auth_project 3、file_mutation 3、authorization筛选2（含向量路由鉴权）。不是全仓C++全量复验。
- 新库迁移、重放、部分schema拒绝及103份旧文件升级检查通过；升级不伪造策略批准或方案。
- 独立[静态审查](review.md)无严重/重要问题。无模型调用、无新依赖、无生产迁移/部署。

## 过程记录

1. [RED](red.txt)：旧schema8/旧代码运行新增8项真实HTTP测试，均因缺少archive-policy接口返回404而失败。
2. [构建](build.txt)：`make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin` 成功。
3. [首轮夹具失败](fixture-failure.txt)：9项中8项通过，1项误将既有DELETE的204按200解析，
   清理又查询已软删除文件导致同场景第二处失败。核对现有路由后修正测试：先取消测试处理任务、
   断言204并从清理列表移除已删文件；没有改业务实现。
4. [最终验证](verified.txt)：修正后使用新临时库重跑，9项HTTP及31项C++全部通过。
5. [资源](resources.txt)：原限额不变，memory.events high始终80521，max/oom/oom_kill均0。
   本轮所有临时服务MainPID=0；测试库由wrapper清理。业务mysqld PID975仍运行，未操作业务库。

HTTP覆盖批准默认拒绝/重放、错误修订和跨项目目录、同源/所有权/角色、持久预览及重启、
修订与待确认项、重复审计防护、两请求竞争仅一条提交、终止态查询、仅改名失效、
整个范围撤销（含未列入方案资料）、源位置变化、真实新版上传、目标改名撤销、
同名既有占位及批内大小写冲突、严格字段/类型/长度/数量。通过文件列表确认源位置和版本不被预览修改。

## 可复现命令及环境

所有重任务在现有 `smartdocsvectorcheck.slice` 串行运行，User=nobody/Group=nogroup、Nice10、
UMask0077、NoNewPrivileges、RuntimeMaxSec900、TimeoutStopSec5、KillMode=control-group。
CPUQuota100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、TasksMax256；
/dev/vda双向5,000,000B/s和200IOPS。运行中检查MemAvailable，低于512MiB需停止本次测试。
本轮观察均高于阈值，未测量/声称进程峰值RSS或吞吐。

副本 `/tmp/smartdocs-safe.h4SzvX/repo`，Python `/tmp/smartdocs-safe.h4SzvX/venv/bin/python`，
PYTHONPATH指向副本python目录。MySQL8.0.46隔离socket `/tmp/smartdocs-mysql.*/mysql.sock`，skip-networking。
本机Python只执行测试，无解析/向量进程、浏览器或DeepSeek调用。

以下命令必须在上述受限单元内执行，不能并行运行：

```bash
make -j1 bin/server bin/smartdocs_tests bin/smartdocs-admin
scripts/test-with-mysql.sh -- /bin/bash -c '
  /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/m3_archive_preview_test.py &&
  stdbuf -oL bin/smartdocs_tests --filter=m3_ &&
  stdbuf -oL bin/smartdocs_tests --filter=mysql_schema &&
  stdbuf -oL bin/smartdocs_tests --filter=auth_project &&
  stdbuf -oL bin/smartdocs_tests --filter=file_mutation &&
  stdbuf -oL bin/smartdocs_tests --filter=authorization'
```

下一步：真实确认/拒绝与等待/继续状态、同事务整批执行与服务端操作ID，再接报告和五工具。
当前API无网页交互，测试不证明模型会正确分类，也未验证写入中断/执行回执丢失等尚未实现的边界。
