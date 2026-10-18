# M3 固定版本分页与物理页范围读取

2026-10-18，北京时间。代码基线cfb2fe20d568f4be2a7ffa3c083353489f720338，
分支codex/m3-mcp-agent。只扩展既有只读入口，不新增依赖、schema、模型调用或
常驻服务；代码与二进制见[SHA-256](source-and-binary-sha256.txt)。

## 功能与范围

POST remote-ai/read与MCP read_document_version.range返回最多10个有序原始
片段；可按PDF物理页筛选，offset相对于所选范围。权限、删除状态和具体版本
批准每次事务内检查，历史引用不跳新版。返回content_ready、has_more、next_offset、
truncated，未解析和无文本页不是相同含义。完整字段与边界见
[C++接口](../../../api/m3-remote-read-api.md)及[MCP工具](../../../api/m3-mcp-read-tools.md)。

## 实际验证

- [实现前RED](red-journal.txt)：C++新增3项均失败于缺失路由；MCP10项中1项
  失败于range尚不支持（其余9项通过）。保留失败证据，不把重跑累计为新分母。
- [增量构建](build-journal.txt)：make -j1构建smartdocs_tests、server、smartdocs-admin成功。
- [C++相关回归](cpp-green-journal.txt)：[49个完整名称](cpp-selection.txt)逐项新进程
  执行，49/49、0失败、0跳过；覆盖本轮3项及processing_jobs_test.cpp全部既有
  处理/读取/搜索/权限用例。不是整个仓库150项C++的全量复验，历史147/147仍属
  前一增量；无关上传/下载等测试本轮没有重跑。schema5、迁移重放/部分迁移拒绝
  及103文件迁移检查不额外计入49。
- [MCP协议](mcp-green-journal.txt)：官方SDK客户端与本地HTTP故障接收器10/10，
  20.791秒。包括范围转发、参数拒绝、身份绑定、错误脱敏和真实socket超时；
  接收器不是C++权限证明。
- [Python与真实链路](python-e2e-journal.txt)：既有Python104/104，21.689秒；
  真实SDK stdio→C++ socket→隔离MySQL→真实解析worker场景2/2，4.126秒
  （不含建库）。新场景拼回15,000字符、13个唯一片段的Markdown，读取中文PDF
  物理页1/2，并在撤销批准后拒绝下一批；原场景继续证明内部资料排除、新版默认
  拒绝、历史固定来源和processing状态。没有用接收器模拟这两个业务场景。
- [独立只读审查](review.md)：最终无Critical/Important。已修复E2E解释器前置
  配置问题、明确解析前页范围语义；保留JSON Schema与C++对1.0验证时点的Minor，
  最终仍被权威C++拒绝，不扩大权限或读取范围。
- [终态与校验](final-status.txt)：全部本轮单元MainPID=0；RED保留失败终态，
  其余均exit0。原MySQL PID975仍运行，源码/二进制SHA全部一致；cmp核对本轮7个
  源码/测试文件与执行副本一致。memory.high离散采样起止80269，max/oom/oom_kill
  均0，无新增事件；采样不代表连续峰值或稳定性承诺。未读取DeepSeek密钥。

## 复现与服务器限制

当前ECS，执行副本/tmp/smartdocs-safe.h4SzvX/repo，串行systemd临时单元，
nobody/nogroup、Nice10、UMask0077、NoNewPrivileges=yes、TimeoutStopSec5、
KillMode=control-group；协议单元120秒，其余900秒。共用smartdocsvectorcheck.slice：
MemoryHigh1200MiB、MemoryMax1600MiB、Swap0、CPU100%、Tasks256，/dev/vda读写
各5MB/s、200IOPS。采样可用内存低于512MiB即停止测试；不放宽限额、不make clean。

单元名：smartdocs-m3-range-red、smartdocs-m3-range-mcp-red、
smartdocs-m3-range-mcp-green、smartdocs-m3-range-build、
smartdocs-m3-range-cpp-green、smartdocs-m3-range-python-e2e。

```bash
make -j1 bin/smartdocs_tests bin/server bin/smartdocs-admin
# 49项名称来自cpp-selection.txt；每项独立进程，遇失败立即退出。
bash scripts/test-with-mysql.sh -- bash -c '
  while IFS= read -r test_case; do
    stdbuf -oL -eL bin/smartdocs_tests --filter="$test_case" || exit $?
  done < docs/evidence/m3/read-range/cpp-selection.txt'
PYTHONPATH=python /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest discover -s test/mcp -v
PYTHONPATH=python /tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -v
PYTHONPATH=python SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  bash scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_mcp_read_test.py
```

以上路径为本次本机已存在的环境；复现机器须换为自己的环境。MCP SDK环境与
pypdf解析环境分开，PDF E2E用SMARTDOCS_TEST_PARSER_PYTHON选择worker解释器；
若同一环境已安装两组依赖，可省略该变量，回退当前解释器。
隔离数据库及场景临时资料由夹具自动清理，无恢复副本，不涉及原业务数据。

## 未完成边界

本轮是有界读取能力，不是完整M3验收。生产内部HTTP、持久Agent任务/取消/预算、
用户确认版本范围、模型出口复核、多步动态模型循环、查询向量接线、三个写工具
及M4归档/报告仍待后续增量；没有远程AI调用或生产部署，不宣称性能/长期稳定性。
