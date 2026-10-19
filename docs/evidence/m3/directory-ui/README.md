# A17 管理员逻辑目录网页

2026-10-19；基线 `a0e253c5a5091226e6f364c694e4586300394752`，分支 `codex/m3-mcp-agent`。
接口与限制见[目录工作区](../../../api/m3-directory-ui.md)，源码/实际被测副本二进制见[SHA256](sha256.txt)。
沿用 project-settings.js、原生控件和已有 C++ 目录 API，不新增依赖、模型调用、迁移或生产部署。

最终前端 **99/99**、页面契约 **8/8**、真实浏览器复验 **1/1**，均无跳过。
浏览器实际两次尝试1成功/1启动失败，不隐藏失败。六个源码/测试文件与受限副本cmp一致，
实际测试使用既有副本二进制，未重建；工作树内旧二进制不是本轮被测对象。

## 前端验证与审查

- [基线](baseline.txt)：前端90/90，页面契约8/8。
- [初始RED](missing-panel-red.txt)：新增目录面板缺失，7失败；既有API适配器1通过。
- [实现后](initial-green.txt)：前端98/98，页面契约8/8。
- [审查](review.md)发现目录变更后旧“已批准”状态文案残留。新增真实app接线测试，
  [首次替身缺少dispatchEvent](approval-harness-failure.txt)，补齐替身后
  [明确复现旧批准文案](stale-approval-red.txt)。失效回调同时清状态与提示后，
  [最终前端99/99、页面契约8/8](unit-green.txt)，无失败/跳过。

覆盖父目录绑定、根不可改名、名称按文本显示、UTF-8字节和非法字符检查、跨项目/角色/注销迟到响应、
只接受最新读取、在途写锁、旧按钮拒绝、响应丢失仅GET恢复，以及改名时选择保持和旧批准文本清除。

## 真实浏览器

使用现有 Chrome/ChromeDriver、非root沙箱，经真实 C++ 和隔离 MySQL；合成两份文件，不启动解析或模型。
场景涵盖reader拒绝、创建/嵌套子目录、改名撤销批准、旧任务409、Agent草稿清理、未移动文件、
上传/目录选择保持、六种屏宽与注销清理。

[首次运行](browser-start-failure.txt)在Chrome会话创建阶段失败，未进入页面业务断言。
[启动诊断](browser-start-diagnosis.txt)记录DevToolsActivePort未生成且无新增OOM；根因未确定。
保留原限额与沙箱复验，不以更大资源或关闭沙箱掩盖失败。

[相同配置复验](browser-green.txt)真实业务1/1通过。屏宽360/375/390/414/768/1280均无目录区域横向溢出，
可见输入框/按钮/select至少44px高；[手机截图](directory-360.png)和[桌面截图](directory-1280.png)已人工复核。
创建两个新目录后改名其中一个，策略从revision1批准变为revision2未批准，旧任务启动409；
Agent草稿与旧批准提示清除，原文件完整元数据不变，归档方案与模型调用计数均为0。

## 复现与资源边界

所有执行单元位于原 `smartdocsvectorcheck.slice`：CPU100%、MemoryHigh1200MiB、Max1600MiB、
Swap0、TasksMax256；磁盘读/写各5MB/s及200IOPS。nobody/nogroup、Nice10、UMask0077、
NoNewPrivileges、RuntimeMaxSec900、KillMode=control-group；同一时间一个串行测试单元。
内存事件基线 high80521/max0/oom0/oom_kill0；可用内存低于512MiB则停止本轮测试。

在既有受限副本 `/tmp/smartdocs-safe.h4SzvX/repo` 执行：

```sh
/tmp/smartdocs-safe.h4SzvX/bin/node --test test/e2e/*_test.mjs
/tmp/smartdocs-safe.h4SzvX/venv/bin/python -B test/e2e/ui_contract_test.py
scripts/test-with-mysql.sh -- /tmp/smartdocs-mcp.P7grAI/venv/bin/python -B test/e2e/m3_directory_browser_test.py
```

PYTHONPATH指向副本python；SMARTDOCS_TEST_PARSER_PYTHON指向既有parser环境；
SMARTDOCS_BROWSER_BIN/SMARTDOCS_WEBDRIVER_BIN指向既有匹配版本浏览器/驱动；
SMARTDOCS_DIRECTORY_BROWSER_OUTPUT为nobody拥有的独占0700临时目录。
数据库包装器仅创建私有临时socket库，32MiB缓冲、禁TCP，验证schema15/迁移重放/部分DDL拒绝和103文件升级，
结束清理该临时库。空root密码初始化警告只属于此库，不影响原业务MySQL。

[资源终态](resources.txt)：所有本轮测试单元MainPID=0，共享组TasksCurrent=0，原mysql仍active；
high80521/max0/oom0/oom_kill0与基线一致，无新增节流或OOM。采样可用内存均高于512MiB停止阈值，
不声称采样等于峰值测量，也不能据此次通过推断间歇启动失败已解决。

本轮只验证目录增量及前端回归，不是全仓C++/Python回归、真实供应商质量、吞吐量或长期稳定性证明。
完整审计、固定任务评测和演示仍待后续，M3/P0未验收。
