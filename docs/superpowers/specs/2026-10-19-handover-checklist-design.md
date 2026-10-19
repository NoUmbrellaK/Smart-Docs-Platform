# 方案A：项目交接清单权威元数据

依据需求第3、4、7.1、8 A-03、9、11节和已批准的方案A。
这是报告子系统的首个可验证增量：管理员维护清单与外发批准，不保存报告、不启动模型。
当前基线ee7b917，继续在codex/m3-mcp-agent已有隔离工作树中实施。

## 取舍与范围

沿用ProjectService、同源会话、MySQL事务和审计。单独一张handover_checklists表，
每项目一行当前修订；不将清单塞进归档目录策略，也不引入通用配置框架、队列或数据库。
文件/归档策略与清单相互独立，更新清单不会生成任务、报告、文件移动或外发请求。
更大的版本历史系统本轮不需要：后续报告应保存所用清单修订及内容快照，并按当前批准重验。
本轮无新UI/MCP工具，普通网页业务API是后续项目设置页的基础，不能算作页面或五工具验收。

## 接口与校验

GET/PUT /api/v1/projects/{project_id}/handover-checklist。
GET要求当前成员Reader以上，无请求体、无查询参数；未设置返回
`{"revision":0,"items":[],"remote_ai_approved":false}`，不回填默认“已批准”模板。
PUT要求Admin、同源Origin、JSON最大65536字节（同时受应用总JSON限制），只接受：

```json
{"expected_revision":0,"confirm_remote_ai":false,"items":[
  {"key":"deployment","title":"部署说明","description":"配置、步骤与回滚说明","required":true}
]}
```

expected_revision必须是0至4294967294整数，不接受布尔/负数/浮点/字符串。
confirm_remote_ai及required必须为布尔；items为0至50项数组，顺序有意义。
每项精确字段key/title/description/required：key匹配`[a-z][a-z0-9_]{0,63}`且项目清单内唯一；
title为1至200 UTF-8字节且非全ASCII空白；description为0至1000 UTF-8字节。
title/description拒绝C0和DEL控制字符，允许普通Unicode与作为数据的标记文字；JSON层验证UTF-8。
校验失败400且不回显输入内容。响应与GET相同三字段，修订由后端维护。

## 修订、审批与事务

沿用成员锁后项目行锁顺序；GET共享锁，PUT独占锁，避免首次插入和并发更新丢失。
expected_revision等于当前修订时写下一修订，同时记录handover.checklist.set审计。
每次成功写入都明确使用本次confirm_remote_ai值，false清空审批者/时间，绝不继承旧批准；
true表示管理员对请求中这份完整清单进行新的批准，不是模型或普通成员的授权。
审批者为认证会话用户、时间为服务器UTC；数据库约束保证批准状态与审批者/时间一致。

只有当前修订恰为expected+1且完整规范JSON等于last_request_json时视为同请求重放，
返回当前结果且不新增修订/审计。其他旧修订返回409 handover_checklist_changed。
同修订不同内容并发只能一个成功；同请求并发返回同一结果。之后已有新修订则不接受更旧重放。
清单写入及审计同事务，故障全部回滚；重启后保留真实清单及批准。
角色每次请求重新检查，降权后即使同内容重放也拒绝；无成员/跨项目返回404，无会话401。

## 持久化与升级

schema11新增handover_checklists(project_id主键、revision、items_json、remote_ai_approved、
approved_by、approved_at、last_request_json)，项目和审批者为外键；revision>0，items为数组且<=50。
升级不回填任何清单或批准；现存文件、任务、归档决定均保持原样。应用与迁移测试统一要求11。
MySQL DDL不承诺整批原子；生产升级仍需先停服务、备份库和文件。本轮只迁移隔离临时库。

## 验证与剩余工作

先用当前schema10真实HTTP观察缺失接口RED；再单线程受限构建，使用全新临时库验证
默认值/重启/重放、显式撤销批准、输入边界、Admin写/Reader读/降权与隔离、同源限制、
两类并发与审计故障回滚；运行迁移及相关C++/归档HTTP回归。
重负载串行，沿用现有CPU100%、MemoryHigh1200MiB/Max1600MiB、Swap0、5MB/s与200IOPS限额。
MemAvailable低于512MiB停止自有测试，无新依赖或模型调用。
后续仍需项目设置网页、绑定清单修订的个人报告、可信模型出口来源依赖、Markdown下载，
以及save_handover_report与其他写工具编排；不以本增量代替完整目标。
