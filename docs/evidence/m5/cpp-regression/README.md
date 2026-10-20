# 当前后端 C++ 回归

2026-10-20，被核对源码提交 `1b2b20f`。本增量回应
[验收索引中的历史版本差异](../acceptance-index.md#版本绑定与发布复验)，
运行现有全部C++单元/集成用例，不改产品、测试断言、schema或依赖。
不是30项真实业务评测、浏览器演示或生产部署。

## 固定范围与构建绑定

[源码和二进制指纹](source-sha256.txt)记录当前工作树与受限执行副本逐字节相同的150个文件：
code、db、third_party、Makefile、scripts、C++测试及支持文件、迁移测试入口。
161个`TEST_CASE`名称均唯一，无完整名称互为子串，避免现有`--filter`子串匹配重复选中。
按名称排序、交替分为[第一组81项](group-1-selection.txt)及[第二组80项](group-2-selection.txt)，
并集必须与源码注册名称一致，不能只选择预计会通过的项目。

[构建核验](build.txt)在受限单元中执行
`make -j1 bin/server bin/smartdocs-admin bin/smartdocs_tests`，三个目标均为up to date，
单元成功退出。没有`make clean`或重新编译；不使用工作树中较旧的bin。
这里绑定的是安全副本的既有构建与当前相同源码，不冒称本轮从零干净构建。

前置普通沙箱无法读取部分旧构建对象；只读提权核对后由nobody执行上述make，
没有放宽目录权限。安全副本docs目录不允许直接创建选择文件，故使用
`/tmp/smartdocs-m5-cpp-group-{1,2}-selection.txt`作为只读644输入；
归档选择文件保存在本目录，与临时输入逐字节一致。

## 执行方式

每个完整名称启动一个独立测试进程，同组串行使用一套临时MySQL；
前一组退出并清理后才启动下一组。并非一次进程内执行161项。
隔离库包装器另外验证schema16、迁移重放、部分DDL拒绝及103文件升级，
这些检查不增加C++用例分母。

当前ECS、安全副本 `/tmp/smartdocs-safe.h4SzvX/repo`，两组分别使用下述命令，
仅替换单元名和选择文件的组号：

```sh
systemd-run --unit=smartdocs-m5-cpp-group1 --slice=smartdocsvectorcheck.slice \
  --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=900 -p TimeoutStopSec=5 -p KillMode=control-group \
  -p IPAddressDeny=any -p IPAddressAllow=localhost \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  /tmp/smartdocs-safe.h4SzvX/repo/scripts/test-with-mysql.sh -- /bin/bash -c \
  'set -eu; while IFS= read -r case_name; do stdbuf -oL -eL bin/smartdocs_tests "--filter=$case_name" || exit "$?"; done < /tmp/smartdocs-m5-cpp-group-1-selection.txt'
```

[第一组运行配置](group-1-running.txt)和[第二组运行配置](group-2-running.txt)保存实际身份与资源设置。
共享slice维持CPU100%、MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、TasksMax256、
磁盘读写各5MB/s和200IOPS；仅允许localhost，nobody/nogroup、Nice10、NoNewPrivileges。
可用内存低于512MiB时停止本轮单元；不调整原业务MySQL，不提高限额。

## 已归档结果

[第一组完整日志](group-1-journal.txt)：2026-10-20 22:51:03—23:04:38（Asia/Shanghai），
81/81个选定C++用例逐一通过，每个进程均为`1 passed, 0 failed, 0 skipped`。
10个迁移PASS单列，不计入81项。单元正常退出；第二组启动前已检查没有nobody身份的mysqld残留。
[第二组完整日志](group-2-journal.txt)：23:05:23—23:19:11，
80/80个选定C++用例逐一通过，每个进程同样为`1 passed, 0 failed, 0 skipped`。
两组分别耗时13分35秒和13分48秒，均未触及15分钟上限；没有重跑或放宽限额。

[逐名核对](verified.txt)确认两组并集恰为源码中的161个用例、无重复或遗漏，
合计**161/161通过、0失败、0跳过**。每组另有10个迁移PASS，合计20个日志标记
不计入161项，也不当作20个不同迁移场景。
过程中一次进度提示误把迁移PASS计入C++，随后已纠正；最终分母由完整选择清单和逐进程RESULT核验。

[终态与哈希](final-status.txt)：两组均success/exit0，测试cgroup已移除，
没有nobody身份的mysqld残留；原MySQL服务仍active。包装器正常退出时清理本轮隔离库，
未删除业务数据。三个安全副本二进制SHA256与运行前一致。
slice的memory.high累计80521保持不变，max/oom/oom_kill均为0；
终态MemAvailable为2089MiB。没有新增OOM不等于已完成负载/长期稳定性测试。

只读审查未发现分组、构建绑定或证据边界的实质问题；审查不启动测试，
最终第二组及161项并集由上述实际日志另行核验。

## 证据边界

此处只核验已有C++测试覆盖范围。它不能替代当前发布候选的真实HTTP/浏览器、
Python解析/模型执行、20轮上传中断重跑、部署配置与完整演示。
SQL/组件测试中的协议向量或脚本故障不是模型语义质量或整机断电。
固定30项任务及全部实际引用语义评审仍未运行；已消费的三次DeepSeek限定授权不再使用。
