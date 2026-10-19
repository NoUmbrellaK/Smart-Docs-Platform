# 受控同源 Agent 执行入口

本增量把已创建的本人 pending 任务交给[既有 CLI/MCP 执行器](m3-deepseek.md)，
不增加数据库、队列、后台自动调度或 MCP Streamable HTTP 服务。C++ schema15 是
业务权威；本页是部署模板，**不表示生产已部署或网页已经有执行按钮**。

## HTTP 契约

`POST /api/v1/projects/{project_id}/agent-tasks/{task_id}/execute`

- 两个 ID 均为32位小写十六进制；无查询参数，请求 JSON 必须严格为 `{}`。
- 使用唯一 `smartdocs_session` Cookie；Host 和 Origin 必须精确匹配管理员配置。
  不接受用户ID、context、路径、密钥、模型、预算、修订或批准参数。
- 只处理 pending：入口先向固定回环 C++ 查询本人任务，再由执行器原子领取。
  管理员不能执行其他成员的私人任务；领取、全部来源/配置批准、每次外发、工具和
  写操作仍由 C++ 校验。预查询不代替领取，也不增加或更改用户批准。
- 同步返回 `{"data":{"state":"completed"}}` 等白名单状态；失败/取消可能带安全错误码。
  HTTP 200 只表示收到执行器返回，不等于业务成功；也可能为 `unknown`，此时仍须
  查询持久状态，不能自动重试。执行器返回后重新鉴权；权限撤销则
  不返回旧结果。回答、报告、方案、轨迹仍由既有 C++ 接口鉴权读取。
- `waiting_confirmation` 表示只生成方案，没有确认或移动文件。此入口不接收 resume，
  不执行已确认归档；使用既有[网页确认与执行](m3-archive-execution.md#网页确认与恢复)
  或显式[本机继续执行](m3-archive-resume.md)。
- 400/401/403/404/409/413/415 拒绝非法输入、身份/权限或非 pending 任务；
  429/503 表示容量或依赖不可用。`agent_outcome_unknown` 表示可能已经领取或提交，
  `retryable:false`；客户端必须查询持久状态，**不得自动重试 POST**。
  即使其他错误标记 retryable，也应先查询状态再由用户决定。取消与响应丢失不等于
  外部请求已撤回或不计费；此入口不保证创建幂等，只复用既有任务的原子运行领取。

## 信任、清理与资源边界

复用检索网关的固定回环监听、严格 HTTP framing、4096字节正文上限、两个连接槽、
无请求日志和 no-store 响应。拒绝重复安全头、chunked、重复 JSON 字段与非有限数字。
5秒 socket 超时是单次 I/O 限制，并非完整慢速连接防御，必须置于成熟同源 HTTPS
代理后。不要公开1318，也不要把该 Python 标准库服务当作公网 HTTP 安全边界。

每次执行在管理员预备的私有运行目录创建0700子目录、0600独占 context；仅写当前
会话、固定后端/origin、路由 ID 和 task_versions。子进程不继承模型密钥。每个请求
创建新的0600脱敏审计文件；审计不含会话、密钥、问题、正文、文件名或模型原始回复。
两目录必须为运行账号所有、精确0700且非符号链接；所有祖先目录及代码必须由可信
管理员控制，不能依赖对最终路径的检查防御可写祖先路径替换。不要持久保存 context。

正常成功、异常及 SIGTERM 关闭均等待已接入执行清理临时 context；关停期间停止接入。
SIGKILL/主机崩溃不能保证 finally 执行，必须使用下方不保留的 RuntimeDirectory；
如不使用 systemd，由管理员在确认旧服务和子进程全部退出后清理专用 runtime 目录。
不得扫描或删除其他业务目录。审计属于持久记录，需单独制定容量监测、备份和保留策略；
本增量不自动删除审计。目录不可写或磁盘满时拒绝/终止，不关闭审计继续运行。

网关最多两个已接入连接，C++全局最多两个运行任务；不新增隐藏队列/重试。
复用执行器180秒工作截止期、最多8次模型/12次工具调用及全部字节/输出限额。
启动握手、前后状态检查及关闭清理另有超时，不宣称整个HTTP请求严格180秒完成。
独立非 root 进程限制地址空间512MiB、文件描述符128、禁core；处理组总限额不提高。
查询编码器为可信可选配置，缺失则明确降级，不改用远程向量服务。

强制终止可能留下C++ `running` 任务；**仅重启网关不会恢复它**。
先查本人状态、结果/回执与脱敏审计。需要放弃时显式取消，再重新创建并确认新任务；
不得把未完成状态当作可重放写入。C++重启会标记 interrupted，但不建议仅为清理任务
重启整个业务服务。进程恢复、网页执行按钮、完整模型审计API和真实供应商效果仍另行验收。

## systemd 模板（未安装）

先部署 schema15 C++、已有 MCP 虚拟环境；复用
[处理资源组](../deployment/m2-vector-worker.md)，不提高同机总预算。
以下为独立服务示例；配置路径、账号与域名由管理员替换。EnvironmentFile仅含
DEEPSEEK_API_KEY，仓库外 root 管理0600，不打印、不放命令行、不提交。
示例把每任务模型请求进一步限制到4次；额度不足将如实失败而非自动追加。

```ini
[Unit]
Description=Smart Docs private Agent execution gateway
After=smart-docs.service
Requires=smart-docs.service

[Service]
Type=exec
User=smartdocs-worker
Group=smartdocs-worker
Slice=smartdocs-processing.slice
WorkingDirectory=/opt/smart-docs
Environment=PYTHONPATH=/opt/smart-docs/python
Environment=SMARTDOCS_PUBLIC_ORIGIN=https://docs.example.com
EnvironmentFile=/etc/smart-docs/deepseek.env
# Optional; omit until the private encoder service is ready:
# Environment=SMARTDOCS_ENCODER_SOCKET=/run/smartdocs-encoder/encoder.sock
RuntimeDirectory=smartdocs-agent
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=no
StateDirectory=smartdocs-agent-audit
StateDirectoryMode=0700
ExecStart=/opt/smart-docs-mcp-venv/bin/python -B -m smartdocs.agent_http --port 1318 --backend-port 1316 --runtime-dir /run/smartdocs-agent --audit-dir /var/lib/smartdocs-agent-audit --max-model-calls 4
Restart=on-failure
RestartSec=10
TimeoutStopSec=300
KillMode=mixed
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LimitCORE=0
MemoryMax=512M
MemorySwapMax=0
TasksMax=32
Nice=10

[Install]
WantedBy=multi-user.target
```

KillMode=mixed先只向主进程发SIGTERM，让MCP子进程随请求有序收尾；主进程最多有
300秒收尾，其退出后或超时后systemd清理残留进程。
RuntimeDirectoryPreserve=no保证服务停止清理时不保留会话文件，
不代表业务任务自动完成。先停本入口并确认退出，再停编码/C++等依赖。
不能使用PrivateNetwork或只允许localhost的IP策略，否则真实供应商HTTPS/DNS不可达；
按组织出口策略另行控制固定供应商访问，不接受客户端指定URL，不跟随重定向。

## 同源 Nginx 模板（未安装）

仅增加精确执行路由；状态/取消/结果等继续代理C++。放在一般 `/api/` 前，并检查
现有 `^~ /api/` 是否遮挡正则 location。拒绝未知Host，外部origin与两服务精确一致。
禁止访问日志记录Cookie/正文；依然沿用M1代理隐藏internal接口。

```nginx
# http级别
limit_req_zone $binary_remote_addr zone=smartdocs_agent_rate:1m rate=4r/m;
limit_conn_zone $server_name zone=smartdocs_agent_connections:1m;

# 既有HTTPS server级别
client_header_timeout 5s;
location ~ "^/api/v1/projects/[0-9a-f]{32}/agent-tasks/[0-9a-f]{32}/execute$" {
    limit_req zone=smartdocs_agent_rate burst=2 nodelay;
    limit_req_status 429;
    limit_conn smartdocs_agent_connections 2;
    limit_conn_status 503;
    client_max_body_size 4k;
    client_body_buffer_size 4k;
    client_body_timeout 5s;
    proxy_request_buffering on;
    proxy_pass http://127.0.0.1:1318;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Origin $http_origin;
    proxy_set_header Connection close;
    proxy_connect_timeout 2s;
    proxy_send_timeout 5s;
    proxy_read_timeout 300s;
    proxy_next_upstream off;
    proxy_cache off;
    access_log off;
}
```

断开浏览器并不会保证取消已开始任务；用户取消须调用既有C++取消接口。
部署前用合成资料验证代理路由、TLS/Cookie、拒绝身份、服务关闭清理与资源限额，
再经授权进行受控真实模型测试。当前证据只验证本地模拟供应商，不证明生产代理已安装。
