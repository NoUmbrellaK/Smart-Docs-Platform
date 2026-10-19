# A29：离线业务判定表与实际引用逐条检查入口

2026-10-19，代码基线 `e2b70cc` 加本提交。新增标准库脚本
[benchmark_review.py](../../../../test/e2e/benchmark_review.py)，不改产品接口、数据库或模型。
它接收操作者已保存的后端结果，为一项冻结任务生成私有判定表，并核对人工评审的统计完整性。
它不是模型执行驱动，不自动运行30项业务任务，不访问模型、网络、数据库或密钥，也不确认归档。

## 输入和使用

在受控目录保存自有 `0600` JSON 快照，顶层字段如下：

- `case_id`：冻结任务ID，如 Q01/H01/A01；不允许自行替换输入与期望结果。
- `run_kind`：`live_provider`、`admission_rejection`、`transport_fixture` 或 `not_run`。
  这是操作者声明，不是供应商调用证明。测试夹具不得标成真实供应商任务。
- `metadata`：非空字符串 `code_revision`、`recorded_at`、`environment`、`model`、`evidence_path`。
  填实际版本/时间/环境/模型标识/运行证据路径，模型未调用时明确写未调用。
  还须留存运行参数、索引/提示词版本、实际ID映射及轨迹供人工核对；本工具不核实这些声明。
- `run_result`：本人 `GET /api/v1/projects/{project}/agent-tasks/{task}/run/result` 的完整 `data`。
  准入拒绝时为 null，并附 `admission_http=403/404`；未执行时为 null。
- `handover_report`：报告任务必须附本人 `GET .../handover-report` 的完整 `data`；
  `id/version/url/markdown_url` 必须与运行结果的报告回执完全一致。不是只保存回执或 PUT 请求体。
- 归档的实际计划预览、人工确认/拒绝、执行回执、最终文件位置等证据可附在快照中；
  本脚本不自动裁定分类质量或事务结果。原始结果及额外证据全部保留在私有判定表的 `snapshot`。

不得放 Cookie、Authorization、API key 或账号密码。资料与模型输出是不可信数据，
使用纯文本编辑器查看，不执行其中的指令、链接或代码。输出可能含正文，**不得提交仓库或公开上传**。

```sh
# 在仓库根目录；输入必须已由操作者保存为自有0600文件，输出路径不得已存在。
python3 -B test/e2e/benchmark_review.py prepare /private/run-snapshot.json /private/review.json
# 人工只填写 review.json 的 business 及每一 citations[].assessment，然后核对：
python3 -B test/e2e/benchmark_review.py summarize /private/run-snapshot.json /private/review.json
```

准备时读取冻结任务及依据，保存两份夹具的文件SHA-256、快照的规范化JSON SHA-256和完整输出。
文件输入最大2MiB，只读自有0600常规文件，拒绝符号链接/FIFO、重复JSON键、NaN/Infinity。
输出独占创建为0600，不覆盖已有判定表。stdout仅打印状态或统计，不打印模型正文、人工理由、路径或凭据。
进程中断可能留下不完整输出；保留供排查并换一个新输出路径，禁止把残缺文件当作完整评审。

## 人工评审口径

- 问答展开 `run_result.result.citations`；交接报告展开每个清单项的 `citations`。
  同一出处被不同结论引用时逐次计数，不去重；非法格式也保留为待判定行，不静默过滤。
- 每行保留原始引用、对应回答/notes和JSON位置。填写 `located`、`supported` 两个布尔值及非空notes；
  前者检查固定文件/版本/位置是否正确，后者检查原文是否支持所关联结论，不能仅凭ID合法给true。
- 两项皆true时 `error=null`；否则记录 `wrong_source`、`wrong_location`、`unsupported_claim`、
  `unavailable_source` 或 `other`。评审者仍须核对错误类别与理由相符。
- `business` 的 `verdict` 初始null，人工可填写 pass/fail/not_run，并给 reviewer 和非空notes。
  pass要求全部引用已评且正确；资料不足、正确拒绝等无引用场景仍须人工按冻结 expected 判断。
  归档执行成功不等于分类正确，单条引用正确也不等于完整任务成功。
- 汇总重建原始行，拒绝删行、改引用/结论/判据、错配快照及夹具版本改变。
  未评完时准确率null；无引用时也为null，不报告100%。保留实际总数、已评数、正确数及错误分类。
- 输出 `live_provider_declared` 只是声明，`execution_verified` 始终false；人工判为pass不是自动验收。
  SHA-256绑定给定文件版本，不是签名，不能发现操作者同时篡改原始快照和评审文件。

这是单项检查入口，不计算30项完成率、不自动补足50条引用，也不生成全部任务成功声明。
正式评测须保留原30项分母、未执行/阻塞/失败项，另计正确拒绝；实际报告引用不足50条时冻结补充任务。
未实现的实际任务运行驱动、外发批准及完整评测仍见[阶段门槛](../stage-gate-audit.md)。

## 验证记录

- 初次新增6个单测均因入口文件尚未实现而失败；实现后6/6通过，均为离线合成快照。
- [边界RED](./boundary-red.txt)：7个方法，遗漏完成回答及not_run误标的2个断言失败；
  同轮2个错误为尚未同步实现的字段重命名（不计成产品漏洞）。修正后7/7通过。
- 只读审查指出“人工声明”被命名为真实证据，以及报告正文未绑定回执的问题。
  前者已改为声明字段；后者[先复现5个拒绝断言失败](./receipt-red.txt)，再校验回执四字段。
- [新增单测最终8/8](./unit.txt)，0.322秒，失败/跳过0。测试真实文件/CLI行为，包含独占输出、
  权限/链接/重复键拒绝、原件不修改、完整分母、无依据引用及漏评不能判通过。

真实供应商API调用数为0；测试内1/3等数字仅验证计数公式，不是平台模型引用准确率。
本轮不把前置数据、合成回答或文件结构检查计为30项业务任务结果。

## 串行回归与真实接口格式验证

[完整日志](./regression.txt)：本地Python **114/114**，21.534秒；随后真实C++ HTTP/隔离SQL
报告用例 **1/1**，2.488秒，均无失败/跳过，整单元退出0。8个新增单测包含在114内，不重复累加。
真实接口用例将本人GET运行结果和GET报告交给prepare/summarize，验证回执匹配、1次引用完整保留、
已评数0、业务判定null、execution_verified=false；它使用已有协议报告夹具，不是实际H01业务评测。
保留该用例原有的reader私有读取、幂等保存、受保护Markdown及权限检查。
不是全部报告14项重跑，也不是全部C++/MCP/浏览器验收。

测试副本 `/tmp/smartdocs-safe.h4SzvX/repo`；三个修改/新增Python文件与工作树cmp一致，
两份冻结输入与副本一致，源码指纹和结束状态见[环境记录](./environment.txt)。
下面是本次启动配置，单元成功退出后systemd回收临时属性，环境记录中的空IPAddress字段不是运行时配置。

```sh
systemd-run --unit=smartdocs-m3-benchmark-review-check --slice=smartdocsvectorcheck.slice \
  --uid=nobody --gid=nogroup -p Nice=10 -p UMask=0077 -p NoNewPrivileges=yes \
  -p RuntimeMaxSec=600 -p TimeoutStopSec=5 -p KillMode=control-group \
  -p IPAddressDeny=any -p IPAddressAllow=localhost \
  --working-directory=/tmp/smartdocs-safe.h4SzvX/repo \
  --setenv=PYTHONPATH=/tmp/smartdocs-safe.h4SzvX/repo/python:/tmp/smartdocs-safe.h4SzvX/repo/test/e2e \
  --setenv=SMARTDOCS_TEST_PARSER_PYTHON=/tmp/smartdocs-safe.h4SzvX/venv/bin/python \
  /bin/bash -c '/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B -m unittest discover -s test/python -p "test_*.py" -v && scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B -m unittest m3_handover_report_test.HandoverReportE2E.test_report_reader_save_exact_replay_private_read_and_safe_markdown -v'
```

复现需先准备同版本受限副本及依赖、选择未占用单元名；不要直接假设上述临时路径存在。
沿用共享slice：单核CPU、MemoryHigh1200MiB/Max1600MiB、Swap0、磁盘读写各5MB/s和200IOPS。
仅独立socket测试MySQL，32MiB缓冲池、performance_schema关闭；没有重编译、提高限额或迁移业务库。
结束MainPID=0，slice TasksCurrent=0，原mysql仍active，MemAvailable1237MiB；
memory.events前后high=80521、max/oom/oom_kill=0，无新增节流/OOM。
wrapper清理本轮临时库与存储，单测TemporaryDirectory清理合成快照；这些测试资料不能恢复，
可按固定脚本再生成，没有删除业务资料。只读复核关闭两项重要问题，未发现新的重要问题。
