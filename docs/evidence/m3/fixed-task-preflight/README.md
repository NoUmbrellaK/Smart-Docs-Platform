# 固定任务前置数据预检（A24，不是模型效果验收）

2026-10-19，代码基线 `36be500ffbec801c693f76e0a5aeabe875d95577` 加本提交新增测试。
测试脚本：[m3_fixed_inputs_test.py](../../../../test/e2e/m3_fixed_inputs_test.py)。
冻结输入及语义判据没有修改；产品代码、数据库schema、模型提示词均未修改。

## 实测结果及失败

- [首次烟测](./attempt-1.txt)：22:21:52–22:23:46北京时间，Q01 **0/1**。
  新测试误用 `GET /api/v1/projects` 得405；核对认证路由后改为 `/api/v1/me` 的projects。
  这是测试驱动错误，不是产品权限缺陷或资源故障；没有绕过鉴权。
- [完整复验](./attempt-2.txt)：22:24:30–22:27:23北京时间，**30/30前置预检**，
  Python测试耗时70.583秒，失败/跳过0，单元退出0；外层建库/迁移/清理时间不计入70.583秒。
- Q01–Q10、A01–A10、H01–H10各10项；27次准入201，Q08/Q09为404，A09为403。
  每项日志含 `business_task: not_run`；真实模型调用与业务任务执行数均为0。
- H03 deployment@2确为pending/pending且无chunk，其他资料已本地解析；
  H05元数据分页10+1，且上传顺序与present一致；Q03指定首次查询为空。
- 静态夹具检查 **4/4**（最终复验0.015秒），`git diff --check` 通过。
  不把这些检查累加为34项业务任务。

预检通过真实HTTP创建项目、角色、目录、文件及版本，逐字节核对下载，验证默认未批准、
具体版本批准、当前指针、解析/行号、目录与清单快照、私有任务访问和scope分页。
admin由套件共用，editor/reader每项独立；全部数据仅在可删除测试库和临时存储中。
全部预检任务取消；SQL只用于验证零model_calls、零计划/确认/报告，没有SQL伪造测试前置状态。
原始日志内的项目/文件/版本/chunk ID是该次临时实例映射，库清理后不是可访问的业务链接。

只读审查修正了装载器按document.id重排、偏离H05冻结顺序的问题，补充顺序断言与日志。
复核未发现新的重要问题。没有产品行为改动，不伪造产品RED/GREEN。

## 环境与复现

Linux 5.15.0-142-generic x86_64，2 CPU，3495 MiB内存，无swap；Python3.10.12，
MySQL8.0.46。只运行一个测试单元；独立MySQL使用32MiB buffer pool、关闭performance_schema、
仅Unix socket；既有wrapper还运行schema16迁移检查。

```sh
systemd-run --unit=smartdocs-m3-fixed-preflight-2 \
  --slice=smartdocsvectorcheck.slice --uid=nobody --gid=nogroup \
  -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=900 -p TimeoutStopSec=5 -p KillMode=control-group \
  -p IPAddressDeny=any -p IPAddressAllow=localhost \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e \
  --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  /tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- \
  /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_fixed_inputs_test.py
```

临时路径为本次已准备的受限副本，不能假设新环境存在；复现先准备同版本依赖与二进制，
选未占用单元名，禁止并行执行。first attempt同配置但用unittest仅选Q01。
共享slice保持单核CPU、MemoryHigh1200MiB、MemoryMax1600MiB、SwapMax0、TasksMax256，
/dev/vda读写各5,000,000B/s、200IOPS；没有提高限额或编译。
systemd确认禁止非loopback网络；没有读取key、启动供应商适配器或远程向量服务。

[环境记录](./environment.txt)含实际指纹、命令和终态。
运行中观测MemAvailable最低1122MiB（采样值，不宣称连续峰值）；结束1232MiB，
两个单元MainPID=0，共享slice TasksCurrent=0，原mysql仍active。
memory.events前后high=80521、max/oom/oom_kill=0，无新增内存high事件/OOM。
wrapper已删除本轮两个临时测试库/文件存储，不能恢复；仓库保存原始输入和完整日志可重新生成。
未删除业务资料、迁移业务数据库或部署生产服务。

## 不覆盖的范围

预检为核对数据读正文、遍历H05全部页，不是模型真实轨迹；业务评测必须重建项目/任务。
不验证模型动态改查、分类/回答质量、人工确认后归档、生成报告、H10报告后撤批准，
也不验证向量召回或实际引用准确率。已验证的行号/chunk存在不等于结论受原文支持。
固定30项业务评测仍未执行；真实API仍需明确批准，M3/P0尚未验收。
