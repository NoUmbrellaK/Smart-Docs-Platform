# M2 同源混合检索入口

这是部署模板，不代表当前机器已经安装生产常驻服务。复用
[向量 worker / 隔离编码服务](m2-vector-worker.md)与 schema 5，
不新增依赖、数据库或远程模型调用。工作台的 Hybrid 模式调用 Python 网关，
名称/关键词模式和固定版本来源读取仍直接调用 C++。

## 信任与资源边界

- 网关 `smartdocs.search_http` 只监听 `127.0.0.1`，只接受一个固定 POST 路由；
  它是受信反向代理后的私有适配器，**不能直接暴露公网或代替成熟 HTTP/TLS 代理**。
- `SMARTDOCS_PUBLIC_ORIGIN` 是管理员配置的精确外部 origin（小写、无尾部斜杠），
  必须与 C++ Secure Cookie 配置匹配。生产用 HTTPS；HTTP 只允许 127.0.0.1 测试。
  传入 Host/Origin 必须同时匹配，忽略所有 Forwarded 头，不提供 CORS。
- 只转发唯一的 `smartdocs_session` 到固定回环 C++ 端口；不接收身份参数、目标 URL、
  路径、查询向量或 mode，不读取 worker bearer、数据库密码、DeepSeek env。
  会话、成员权限、目录、当前版本和批准边界全部由 C++ 判定。
- 请求最多 4096 字节，最多两个已接入连接、无无限工作线程池；繁忙即 503 search_busy。
  不接受 chunked body、重复安全头或重复 JSON 字段；连接不复用，响应 no-store。
  socket 读写超时 5 秒是单次 I/O 限制，不是抗慢速连接的完整防护；必须同时启用下方
  代理限流、连接上限、请求缓冲和超时。
- C++ 单次连接超时 15 秒，响应最多 256 KiB；不跟随重定向、不自动重试、不转发上游
  原始错误正文。编码复用 75 秒截止期的私有 Unix 客户端以及全局单模型锁。
  先 C++ 预检，再编码，最后重新向 C++ 查询；编码失败仍重新查询并显式降级，
  不返回预检缓存，不扩大到未批准资料。整个请求并非严格 105 秒墙钟 SLA。
- 网关使用与 encoder socket 一致的非 root 账号；代码/模型由管理员管理，不能由该
  账号修改。512 MiB 地址空间、64 个文件描述符，和解析/向量 worker 共用处理资源组。
  浏览器切换条件会忽略旧响应，但不声称取消已在途的有界模型计算。

## systemd 模板

先按向量服务手册准备私有 socket 与 `smartdocs-processing.slice`。以下是
`/etc/systemd/system/smartdocs-search.service` 的完整新服务内容，
不是可以直接合并进别的服务的 drop-in。不使用包含其他秘密的 EnvironmentFile。

```ini
[Unit]
Description=Smart Docs private hybrid search gateway
After=smart-docs.service smartdocs-encoder.socket
Requires=smart-docs.service smartdocs-encoder.socket

[Service]
Type=exec
User=smartdocs-worker
Group=smartdocs-worker
Slice=smartdocs-processing.slice
WorkingDirectory=/opt/smart-docs
Environment=PYTHONPATH=/opt/smart-docs/python
Environment=SMARTDOCS_PUBLIC_ORIGIN=https://docs.example.com
Environment=SMARTDOCS_ENCODER_SOCKET=/run/smartdocs-encoder/encoder.sock
ExecStart=/opt/smart-docs-venv/bin/python -B -m smartdocs.search_http --port 1317 --backend-port 1316
Restart=on-failure
RestartSec=10
TimeoutStopSec=5
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_INET
IPAddressDeny=any
IPAddressAllow=localhost
MemoryMax=256M
MemorySwapMax=0
TasksMax=8
Nice=10

[Install]
WantedBy=multi-user.target
```

网关不能用 `PrivateNetwork=yes` 隔离到独立回环网络，否则无法访问宿主 C++。
网络策略依赖宿主 systemd/BPF 支持，部署时验证实际生效；编码服务仍必须禁用 IP socket。
不提高已有处理组的总内存、CPU 和 I/O 上限。停止服务时先网关/worker，再 socket 与
所有活动编码实例，最后 C++；不能只停 socket 就假定模型进程已停止。

## Nginx 接线模板

在既有 HTTPS 站点的配置中增加以下内容；域名/证书/账号路径按实际环境替换。
其他路由仍沿用 [M1 同源代理](m1-single-host.md)。不要公开 `/internal/` 接口。
代理应拒绝未知站点 Host；外部 origin 必须和上面配置完全一致（含非默认端口）。

```nginx
# http 级别；不要放进 location
limit_req_zone $binary_remote_addr zone=smartdocs_search_rate:1m rate=6r/m;
limit_conn_zone $server_name zone=smartdocs_search_connections:1m;

# 以下放进既有 HTTPS server
client_header_timeout 5s;
location ~ "^/api/v1/projects/[0-9a-f]{32}/hybrid-search$" {
    limit_req zone=smartdocs_search_rate burst=2 nodelay;
    limit_req_status 429;
    limit_conn smartdocs_search_connections 2;
    limit_conn_status 503;
    client_max_body_size 4k;
    client_body_buffer_size 4k;
    client_body_timeout 5s;
    proxy_request_buffering on;
    proxy_pass http://127.0.0.1:1317;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Origin $http_origin;
    proxy_set_header Connection close;
    proxy_connect_timeout 2s;
    proxy_send_timeout 5s;
    proxy_read_timeout 120s;
    proxy_cache off;
    access_log off;
}
```

不要把普通 `location /` 改成 `^~` 而屏蔽这条正则路由；保留已有内部接口拒绝规则。
代理自身的 413/429/503 可以是非 JSON，页面会显示请求失败，不会擅自退回更宽的
关键词范围。网关返回的降级则是 HTTP 200、retrieval=keyword_only 和明确 warnings。

上线前运行 `nginx -t`、`systemd-analyze verify`，核对实际监听仅回环及 socket 权限。
通过公网 HTTPS 登录后的工作台执行 Hybrid 查询、固定版本来源读取；撤销批准后重查
应不再返回该文件；停编码实例验证同范围关键词降级。未配置网关时 Hybrid 会失败，
其他两种搜索不受影响。模板未经真实域名/TLS/Nginx 部署验收，不能据此宣称已经上线。

接口与错误码见 [搜索 API](../api/m2-search-api.md#工作台混合检索网关)。
