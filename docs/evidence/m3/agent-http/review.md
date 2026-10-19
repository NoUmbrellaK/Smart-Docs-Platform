# 只读审查与处置

基线 `e8c0740801f2dfd8a0e4274759b3f3975dfcb936`，审查新增 Agent HTTP、共享检索
framing、单测、真实链路测试及部署文档。审查不执行测试、外部网络或修改文件。

- 重要问题：原实现继承 daemon_threads=True，且主入口无SIGTERM处理；正常停止
  可能跳过TemporaryDirectory清理，遗留0600但仍包含会话的context。
  新增活动请求关闭及子进程SIGTERM测试，先复现提前关闭和退出码-15。
  修复为非daemon请求线程、独立线程shutdown、server_close等待清理，再恢复信号处理器；
  两测试及入口10项随后通过。SIGKILL不依赖finally，文档要求不保留RuntimeDirectory。
- 执行过程中ValueError原本被误标400；先复现期望503实际400，之后仅在请求解析
  阶段映射400，执行异常按unknown处理且不自动重试。入口测试通过。
- 复核未发现新的重要问题：严格固定路由/Origin/Cookie/空JSON，C++保有身份、
  scope和原子领取权威，临时私有context、脱敏响应和后置鉴权符合边界。
- 联调替身只位于HTTPX供应商网络层；真实HTTP、执行器、stdio MCP、C++和临时库
  保持实际运行。审查意见不代替联调实际结果，结果另附。
- 文档收紧两点：HTTP200也可能是unknown，仍需只读查状态；KillMode=mixed在
  主进程退出后或停止超时后可清理残留进程，并非必须等待完整300秒。

沿用最小实现原则，复用现有HTTP校验和CLI，不增加依赖、业务授权规则或自动队列。
没有要求将调试适配器暴露公网；systemd/Nginx均仅为未安装模板。
