# M5 当前组件部署接线核对

2026-10-21，代码基线`2bbe48e`。补齐[P0单机部署入口](../../../deployment/p0-single-host.md)，
保留已有详细模板，统一schema16、组件/依赖/端口、凭据与路径、迁移备份、
启动/关停、同源路由及发布检查。没有新增安装器、产品代码、依赖或生产配置。

修正两个可复现文档问题：

- 解析worker手册仍称当前schema13，实际应用启动和readiness均检查16，
  迁移文件001–016连续；旧M1/schema2、M2/schema5保留为明确历史基线。
- 解析worker的`After/Requires=smartdocs.service`与M1主服务
  `smart-docs.service`不一致。模板已统一带连字符名称；没有改本机已安装unit。

## 实际检查

[CLI原始输出](cli-help.txt)：六个`--help`均退出0，包括worker、vector_worker、
search_http、agent_http、agent_cli和mcp_read。以nobody运行，先清空环境，
仅传PATH及安全副本PYTHONPATH；每个子命令最多15秒，串行，不启动服务或业务任务。
前三项使用既有parser venv，后三项使用既有MCP venv。

复现方式（替换已准备的venv路径；不要加载秘密环境文件）：

```sh
env -i PATH=/usr/bin:/bin PYTHONPATH=/path/to/checkout/python \
  /path/to/parser-venv/bin/python -B -m smartdocs.worker --help
env -i PATH=/usr/bin:/bin PYTHONPATH=/path/to/checkout/python \
  /path/to/mcp-venv/bin/python -B -m smartdocs.agent_http --help
```

同法替换另外四个模块；`--help`只证明入口与参数可导入，不证明服务运行。

[源码/配置核对](source-audit.txt)：

- 全18个Python模块与实际CLI执行的受限副本逐字节一致，保存SHA-256。
- server两处要求schema16，仓库迁移序列为001–016；没有执行任何迁移。
- parser两个已锁包、MCP30个已锁包的已安装版本与requirements匹配；
  本轮没有重新验证已安装wheel字节或embedding依赖，也没有安装或下载。
- 本机PATH没有nginx；systemd-analyze存在。本轮没有用不存在的
  `/opt`部署路径伪装成成功的unit校验，没有执行`nginx -t`、TLS或常驻服务组合检查。

独立只读审查核对文档与源码/现有模板，未发现关键或重要问题；
同样没有执行模型、数据库或部署测试。
归档检查另确认302个本地链接目标存在、新手册两个shell代码块通过`bash -n`、
六份帮助记录齐全，`git diff --check`通过；产品源码、配置、迁移、资源及测试文件无修改。

## 不覆盖

没有读取真实API key、付费调用、启动MySQL、运行模型、改变端口/服务或业务数据。
不把六个帮助命令计入30项业务任务或产品回归分母，不宣称全套部署已通过。
文档里的安装、迁移和启动命令本轮均未执行。

当前发布剩余项仍包括实际受控部署的unit/代理/TLS/权限/资源验证、
27项模型任务及全部业务和引用评审、真实确认归档与完整演示。
M5/P0仍未完成；此前三次限定模型授权不因新增部署手册而恢复或扩大。
