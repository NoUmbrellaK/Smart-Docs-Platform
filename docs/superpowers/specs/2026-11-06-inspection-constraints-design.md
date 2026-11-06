# 显式任务检查限制（已批准方案A）

用户于本轮批准“增加明确选项并由服务端强制执行”。此设计不从自然语言、文档或模型输出推断授权，
不声称旧H05已修复。原30项冻结输入和历史台账保持不变；新增约束任务单独验证。

任务创建可选字段：
```json
{"inspection_constraints":{"mode":"metadata","max_page":1,"page_size":10}}
```
省略字段保持旧客户端与旧任务行为；显式null/未知字段/错误类型拒绝400。
mode只允许metadata，max_page整数1..1000，page_size整数1..10，不接受bool/浮点。
字段仅在任务创建时由已认证用户提交；任务内不可更新。三种任务类型均可使用。
迁移17新增nullable JSON列，旧行NULL，不生成历史选择或修改文件外发批准。

C++在现有同一鉴权/任务锁/事务中强制检查：
- 有限制的任务只允许metadata搜索；有效page不得超过max_page，有效page_size不得超过page_size上限。
  仍使用既有默认page=1/page_size=10；用户上限小于默认时必须明确传入较小page_size。
- 该任务的read_document_version全部拒绝（包括metadata/status、chunk、range），避免绕过搜索页范围。
- 拒绝403/task_inspection_restricted，返回固定无正文消息，经原工具审计记录access_denied。
- 不改变项目成员权限、版本有效性、外发批准、取消、幂等、来源台账和归档真实确认。
- 普通文件阅读/下载不受此任务限制；只约束该任务的工具入口。

Get/List/StartRun仅对有限制任务返回原结构化约束。Python从StartRun读取并严格验证，
向模型传达可信约束；隐藏read工具、将search.mode限制metadata、page上限、page_size上限；
要求page_size显式给出，以免默认值超过较小上限。不修改全局SCHEMAS。
服务端仍是权威：直接HTTP/MCP调用不能绕过；Python的限制只是减少无效选择。
保存报告/创建方案工具保持原语义，不能把选择元数据模式等同于批准归档。

网页：显式“Metadata search only”选择及最大页数/每页上限原生数字输入，默认不勾选，
启用时默认1页/10项。改变任一约束撤销外发勾选；创建中锁定；新草稿/切项目重置。
创建payload携带明确字段；任务详情显示已持久化约束。窄屏单列、可见label、触控尺寸不缩水。

验证：先证明旧行为无硬限制；后做实际HTTP/隔离SQL、迁移/重启、直接MCP绕行、Python动态schema、
网页请求/重置/失效控制。零真实模型调用。不能以通过这些用例宣称原自然语言H05或业务P0通过。
