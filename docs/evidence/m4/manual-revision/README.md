# M4 人工调整归档方案

2026-10-20，基线 `c044754cfe940a277b53f5d833007f15525c0a0b` 加本目录同提交的变更。
复用既有PUT、权限与修订控制，只增加网页人工调整入口；没有新增依赖、schema或模型请求。
按最小实现原则使用原生表单及已有样式，不创建另一套方案服务。

## 范围与结果

固定文件、版本和源目录，只调整获批目标或显式待定目标，以及非空理由。
保存创建未确认的新修订；必须重新勾选确认，再独立执行。
同名目录用名称与稳定ID区分；迟到响应、旧按钮、权限变化及在途重复点击不能恢复旧表单。
未知响应只提示刷新权威方案，不自动重试或查询并不存在的修订保存回执。

- [功能RED](./feature-red.txt)：47通过、6失败，均为缺少人工调整功能的断言。
- [首次GREEN中的测试错误](./first-green-test-error.txt)：52通过、1失败；
  测试错误地期待在途写锁期间仍能打开详情，修正测试以验证详情保持空白，未放松产品锁。
- [同名目录RED](./labels-red.txt)：0/1，原标签无法区分；修正为名称与ID后纳入最终回归。
- [最终前端回归](./unit-final.txt)：108/108，1264.116931毫秒；UI合同8/8，0.010秒，0跳过。
- [最终真实Chrome/C++/MySQL](./browser-final-and-status.txt)：1/1，11.737秒，
  拒绝→人工调整目标/理由→PUT新修订→未勾选确认→确认不移动→独立执行→回执通过。
  六宽度、同名目标及原有任务/引用/来源撤销场景通过；该1项是复合场景，不是业务任务集完成率。

独立只读审查提出同名目录及未知响应提示两项问题；修正后复审无遗留阻断项，
最终验收仍取决于真实浏览器结果。没有为通过测试放宽权限、策略或修订校验。
最终结果已通过，归档并目视检查[360宽编辑器](./edit-360.png)及[1280宽编辑器](./edit-1280.png)：
人工操作说明、固定标识、理由纯文本及保存按钮可读；窄屏原生选择框关闭时会裁切长选项，
完整选项标签包含名称与ID，实际选择及提交ID已经浏览器断言核实。

## 浏览器失败与诊断

[首轮日志](./browser-first-failure.txt)：1项错误，108.306秒，发生于Chrome创建会话，
尚未执行页面断言；不能计为产品流程通过或归档业务失败。
随后仅运行无页面登录、无凭据的[启动诊断](./startup-diagnostic.txt)，同限额下会话成功建立。
首次失败的具体原因未确定，不据此宣称已修复Chrome问题或需要扩容。
第二轮 `smartdocs-m4-edit-browser-final` 已成功启动Chrome，但[旧夹具失败](./browser-fixture-failure.txt)：
1项失败、24.313秒，`/run/check`无body被400拒绝。当前接口要求`{"tool_request_ids":[]}`，
已有来源回执HTTP测试明确验证无body必须拒绝。本轮修正夹具，不更改后端约束。
第三轮 `smartdocs-m4-edit-browser-verified`：[布局断言失败](./browser-hidden-control-failure.txt)，
1项失败、7.999秒。旧测试将普通问答模式下隐藏的配置按钮计为0px触控控件；
参照已有task-mode/directory浏览器测试，工作区尺寸检查仅计实际有布局框的元素。
可见控件44px标准及新编辑器的全部控件检查不变，未修改产品CSS。
之后单次验证使用下述新单元，不配置自动重试；所有失败均保留。

## 环境与复现

沿用[前一增量副本核对](../boundary-recheck/preflight.txt)的C++二进制及隔离环境，
本次仅同步前端与测试文件。浏览器初始方案与问答为人工协议夹具，不是模型回答；
新修订必须由真实网页选择目标、填写理由和点击保存产生，夹具不再直接PUT第二修订。
用不同父目录下的两个同名目标验证选择的稳定ID；360/375/390/414/768/1280六宽度检查溢出
及44px触控尺寸。模型分类、真实业务质量、全仓回归和生产部署不在本轮分母内。

[实际运行配置](./visible-running.txt)（[第二轮](./running.txt)、[第三轮](./verified-running.txt)）：nobody/nogroup、Nice10、UMask0077、NoNewPrivileges，
15分钟上限、control-group回收，仅允许回环网络。CPU100%、内存软/硬1200/1600MiB、
Swap0、磁盘读写各5MB/s及200IOPS均保持不变；不加载DeepSeek环境文件。

在 `/tmp/smartdocs-safe.h4SzvX/repo` 的受限单元中串行执行：

```sh
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/*_test.mjs
/usr/bin/python3 -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_agent_browser_test.py
```

浏览器单元 `smartdocs-m4-edit-browser-visible`，输出目录 `/tmp/smartdocs-m4-edit-visible.nnppkr`；
PYTHONPATH包括安全副本的python与test/e2e，解析器使用安全副本venv，
Chrome/Driver分别为安全目录下chrome-linux64/chrome与chromedriver-linux64/chromedriver。
空root密码警告仅属于临时Unix-socket测试库，不修改业务库。

最终单元22:01:30—22:03:24 CST，包含限速数据库初始化、迁移及清理，
不是业务请求SLA。启动活动采样MemAvailable2146328KiB，结束2210652KiB，
这些不是连续最低值；全轮memory.events的high80521及max/oom/oom_kill均保持不变，
没有新增内存节流/OOM。最终MainPID0、TasksCurrent0、原mysql active。
测试wrapper清理了自己的临时库和人工样本，不能直接恢复；未删除业务数据。

工作树与实际执行副本的四文件SHA256一致：

```text
ba5ecbacbe9f80d617e1b5645442ccecd80a436e49974e1aa899c951b604ad67  resources/js/agent.js
158c159b28a801e12c96dce8dd8c31d9dff4310b8a08bfb562de4c2fa91b97a7  resources/js/api.js
75f57637268d2f6a3ae764442702e85cadc7650fbabeca1bc978aa7a622b96b6  test/e2e/agent_ui_test.mjs
4e75017fdefad9251ba6e6543f48f2313727ab8d1372b6bddf11a6ff3ff998f2  test/e2e/m3_agent_browser_test.py
```

M4与整个P0仍未关闭；此入口不等于自然语言模型修订，也不消费或续用先前三次限定API授权。

归档检查：四份被测文件与安全副本哈希一致，203个本地文档链接目标存在，
凭据模式扫描无匹配、`git diff --check`通过；这些检查不增加产品测试分母。
首次暂存后发现RED日志自带6处行尾空白，已清除并复核；不改变断言或测试结果。
