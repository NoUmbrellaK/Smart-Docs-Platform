# M1 单机部署手册

本文说明 M1 C++ 文件服务的单机部署。M1 包含登录会话、项目与成员角色、逻辑目录、分片上传、不可变版本、软删除/还原、远程 AI 授权标记和受保护下载；不包含 M2 解析/检索、M3 Agent/MCP 或 M4 归档报告。

## 已验证工具链快照

2026-09-15 的开发主机使用 Ubuntu 22.04 系列环境：

| 组件 | 版本 |
| --- | --- |
| Linux kernel | 5.15.0-142-generic x86_64 |
| GCC/G++ | 11.4.0 |
| GNU Make | 4.3 |
| MySQL Server/Client | 8.0.46 |
| libmysqlclient | 8.0.46 |
| OpenSSL/libssl-dev | 3.0.2 |
| Python（仅测试/运维脚本） | 3.10.12 |

这张表记录已使用的版本，不代表经过证明的最低版本。数据库迁移使用 MySQL 8 的排序规则、生成列、CHECK 约束和 CTE；部署时使用 MySQL 8.0，不要替换成未经验证的 MariaDB。

Ubuntu/Debian 构建依赖：

```bash
sudo apt update
sudo apt install build-essential libmysqlclient-dev libssl-dev mysql-server mysql-client
```

## 目录与系统账号

下文假设代码 checkout 位于 `/opt/smart-docs`，服务用户为 `smartdocs`：

```bash
sudo useradd --system --home /var/lib/smart-docs --shell /usr/sbin/nologin smartdocs
sudo install -d -m 0755 -o root -g root /opt/smart-docs
sudo install -d -m 0750 -o root -g smartdocs /etc/smart-docs
sudo install -d -m 0700 -o smartdocs -g smartdocs /var/lib/smart-docs
sudo install -d -m 0700 -o smartdocs -g smartdocs /var/lib/smart-docs/objects
sudo install -d -m 0700 -o smartdocs -g smartdocs /var/lib/smart-docs/staging
```

`SMARTDOCS_STORAGE_ROOT` 必须是绝对且非根路径，根目录、`objects/` 和 `staging/` 不得允许 group/other 写入，也不能是符号链接。服务启动时会检查这些条件。

## 构建

在 checkout 中构建生产服务器和管理工具：

```bash
cd /opt/smart-docs
make -j1 server admin
```

小规格主机不要使用高并行构建。完整 M1 验收还会初始化隔离 MySQL 并执行二十轮真实故障恢复；运行前先阅读[资源耗尽事故记录](../operations/2026-09-15-m1-acceptance-resource-exhaustion.md)。生产部署不需要 `test-server`，也绝不能设置 `SMARTDOCS_FAULT_POINT`。

## MySQL 初始化与迁移

以下 SQL 创建独立数据库和应用用户。把示例密码替换为随机密码，不要把密码写入 shell 历史或仓库：

```sql
CREATE DATABASE smart_docs
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'smart_docs'@'localhost' IDENTIFIED BY '<random-password>';
GRANT ALL PRIVILEGES ON smart_docs.* TO 'smart_docs'@'localhost';
FLUSH PRIVILEGES;
```

迁移由 `scripts/migrate.sh` 显式执行；普通服务器启动不会修改 schema。当前服务要求 schema 版本 `2`。如果迁移版本缺失但 M1 表已经存在，脚本以 `partial_schema` 退出，必须从备份恢复或重建空数据库，不能跳过错误继续启动。

## 受保护的环境文件

复制模板到仓库外，并限制为 root 可写、服务组可读：

```bash
sudo install -m 0640 -o root -g smartdocs \
  /opt/smart-docs/config/smart-docs.env.example \
  /etc/smart-docs/smart-docs.env
sudoedit /etc/smart-docs/smart-docs.env
```

2 vCPU 单机建议从以下关键值开始，再按实际监控调整：

```dotenv
SMARTDOCS_LISTEN_ADDRESS=127.0.0.1
SMARTDOCS_PORT=1316
SMARTDOCS_THREAD_COUNT=2
SMARTDOCS_CONNECTION_TIMEOUT_MS=60000

SMARTDOCS_MYSQL_HOST=127.0.0.1
SMARTDOCS_MYSQL_PORT=3306
SMARTDOCS_MYSQL_DATABASE=smart_docs
SMARTDOCS_MYSQL_USER=smart_docs
SMARTDOCS_MYSQL_PASSWORD=REPLACE_ME
SMARTDOCS_MYSQL_POOL_SIZE=4

SMARTDOCS_STORAGE_ROOT=/var/lib/smart-docs
SMARTDOCS_MAX_FILE_BYTES=1073741824
SMARTDOCS_CHUNK_BYTES=8388608
SMARTDOCS_MAX_JSON_BYTES=1048576
SMARTDOCS_SESSION_SECONDS=43200
SMARTDOCS_PASSWORD_ITERATIONS=210000
SMARTDOCS_SECURE_COOKIE=true
SMARTDOCS_LOG_LEVEL=1
```

使用本机 Unix socket 时设置绝对路径 `SMARTDOCS_MYSQL_SOCKET=/run/mysqld/mysqld.sock`；迁移脚本和服务随后忽略 TCP 端口。生产 HTTPS 部署必须把 `SMARTDOCS_SECURE_COOKIE` 设为 `true`。

以服务用户加载同一份配置并迁移：

```bash
sudo -u smartdocs bash -c \
  'set -a; . /etc/smart-docs/smart-docs.env; set +a; /opt/smart-docs/scripts/migrate.sh'
```

成功输出应为 `schema_version=2`。

## 创建首个管理员账号

服务没有匿名注册接口。通过受保护的标准输入创建首个账号；用户名为 3–64 字符，密码为 12–1024 字符：

```bash
read -rsp 'Initial admin password: ' SMARTDOCS_INITIAL_PASSWORD
printf '\n'
printf '%s\n' "$SMARTDOCS_INITIAL_PASSWORD" | \
  sudo -u smartdocs bash -c \
  'set -a; . /etc/smart-docs/smart-docs.env; set +a; exec /opt/smart-docs/bin/smartdocs-admin create-user --username admin --password-stdin'
unset SMARTDOCS_INITIAL_PASSWORD
```

成功输出仅包含 `created_user_id=<32位ID>`。重复用户名以退出码 `2` 和 `username_conflict` 拒绝。

## systemd 服务

创建 `/etc/systemd/system/smart-docs.service`：

```ini
[Unit]
Description=Smart Docs Platform M1 file service
After=network-online.target mysql.service
Wants=network-online.target
Requires=mysql.service

[Service]
Type=simple
User=smartdocs
Group=smartdocs
WorkingDirectory=/opt/smart-docs
EnvironmentFile=/etc/smart-docs/smart-docs.env
ExecStart=/opt/smart-docs/bin/server
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
UMask=0077
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/smart-docs

[Install]
WantedBy=multi-user.target
```

加载、启动与停止：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now smart-docs
sudo systemctl status smart-docs
sudo systemctl stop smart-docs
```

启动失败时先看 `journalctl -u smart-docs`。缺失配置、数据库不可达、schema 不是 2、存储权限不安全或普通服务器检测到 `SMARTDOCS_FAULT_POINT` 时，进程会在监听业务端口前退出。

健康检查：

```bash
curl --fail http://127.0.0.1:1316/api/v1/health/live
curl --fail http://127.0.0.1:1316/api/v1/health/ready
```

`live` 只证明进程/事件循环可响应；只有数据库、schema 和存储均可用时 `ready` 才返回 200。

## HTTPS 反向代理

生产环境只向本机暴露 C++ 端口，由 Nginx/Caddy 等代理终止 TLS。不要把上传目录映射成静态目录。Nginx 的关键配置如下，证书配置由站点证书流程管理：

```nginx
server {
    listen 443 ssl http2;
    server_name docs.example.com;

    client_max_body_size 9m; # 必须大于 SMARTDOCS_CHUNK_BYTES

    location / {
        proxy_pass http://127.0.0.1:1316;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header Connection "";
        proxy_request_buffering off;
        proxy_read_timeout 120s;
    }
}
```

应用用 `Origin` 与 `Host` 做同源校验。代理必须保留公开 `Host`；`SMARTDOCS_SECURE_COOKIE=true` 时应用期望 `Origin: https://<Host>`。若修改分片大小，应同步调整代理请求体上限。

## 备份与恢复

一次可恢复备份至少包含：

- MySQL `smart_docs` 数据库；
- 完整 `SMARTDOCS_STORAGE_ROOT`（包括不可变对象和未完成任务的已确认分片）；
- 部署使用的 Git 提交号；
- 受保护环境文件的独立加密备份。

最简单的一致性流程是先停止应用写入，再备份数据库和存储。数据库凭据通过受保护的 MySQL option file 或本机 socket 认证提供，不要放在命令行：

```bash
sudo systemctl stop smart-docs
sudo mysqldump --single-transaction --routines --triggers smart_docs \
  > smart-docs.sql
sudo tar --acls --xattrs -C /var/lib -cpf smart-docs-storage.tar smart-docs
git -C /opt/smart-docs rev-parse HEAD > smart-docs-revision.txt
sudo systemctl start smart-docs
```

恢复时停止服务，恢复数据库与整个存储根，恢复正确所有者/`0700` 权限，checkout 到记录的提交，重新执行迁移，再启动并检查 `ready`。数据库与存储必须来自同一备份窗口；只恢复其中一项可能导致版本对象缺失或不可见孤儿。

## 发布前检查

- `bin/server` 与 `bin/smartdocs-admin` 来自目标提交；
- 环境文件不在仓库中且权限为 `0640` 或更严格；
- 存储目录归 `smartdocs` 所有且没有 group/other 写权限；
- `scripts/migrate.sh` 输出 `schema_version=2`；
- 普通服务器没有 `SMARTDOCS_FAULT_POINT`；
- 后端只监听环回地址，公网入口使用 HTTPS；
- `live` 和 `ready` 均返回 200；
- 数据库与整个存储根已纳入同一备份流程。
