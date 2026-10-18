# M3 官方 MCP 只读协议增量

承接 c90f945 的 C++ 外发门禁；用户已授权按 M3 设计自主继续。该增量为新协议接入层，采用官方 SDK，不自研 JSON-RPC，也不在 Python 复制权限数据库。

## 选择与边界

当前 Python 3.10；锁定官方仍维护的 mcp 1.30.0 及本机解析的全部 wheel 依赖、SHA256。独立环境安装，不改 worker/向量环境。v2 需 Python 3.11，暂不为此改系统 Python。来源：https://github.com/modelcontextprotocol/python-sdk/tree/v1.x 、https://pypi.org/project/mcp/1.30.0/ 。

先提供需求允许的 stdio 调试入口，仅父进程可使用，拒绝匿名监听。生产内部 Streamable HTTP、持久任务身份与用户范围确认仍须后续实现；此调试能力不能直接接远程模型。可信父进程提供绑定的项目 ID、任务 ID、会话 token、loopback C++ 端口及 Origin；从权限为 0600、当前用户所有的普通配置文件读取，不允许模型参数覆盖，不把 token 放入命令行或输出。启动配置明确 scope=project_approved_versions：只允许该项目中仍经 C++ 批准的版本，不承诺已实现选定文件子集。

只注册 search_documents 和 read_document_version。前者保持 metadata/keyword/hybrid、分页最多10项；hybrid 本增量不加载编码器，C++ 明确返回 keyword_only 降级。后者读固定版本状态或指定一个稳定 chunk_id，保留原始来源，不隐式跳新版。页范围/整篇分段读取尚未实现，不用该子集冒充完整读取合同。其余三个写工具不注册空壳。

## 信任与结果

工具参数 JSON schema 设 additionalProperties=false，ID 为固定十六进制格式，长度/页数限制明确。实际调用仍执行 schema 校验，不依赖客户端遵守 schema。每次调用生成 request_id，绑定 task_id，记录 duration_ms 和结构化状态；不回显无效参数、上游错误正文、Cookie、内部路径或异常字符串。权限/资源不存在统一 access_denied；400参数错误，409冲突，429繁忙，超时单列，其他故障dependency_failed。不可用/未解析状态保留 C++ 结果，不能伪造正文已读取。

网络只用固定127.0.0.1端口，不受代理环境影响，不跟随重定向；请求仅走 remote-ai 路径，响应最多256KiB，超时15秒，无自动重试或结果缓存。SDK 协议校验失败只返回通用错误，避免默认校验错误回显身份类额外字段。开发入口不会接受任意 URL、方法、文件路径或写操作。

## 验证

先通过真实 SDK 客户端握手、列工具和调用观察缺失模块导致的测试失败，再实现。单元层用本地 HTTP 故障接收器验证实际请求路径/身份绑定、参数拒绝、错误脱敏、大小限制和重定向拒绝；不把接收器当 C++ 权限证明。集成层使用真实 C++/隔离 MySQL/解析 worker及 SDK stdio客户端，验证内部/获批资料、固定片段、撤销批准及未知工具拒绝。

所有安装、构建、测试串行运行于现有 smartdocsvectorcheck.slice；保留原内存/CPU/IO限额及低内存停止阈值。无 DeepSeek 调用，不升级生产常驻服务。完整 M3、T-18 模型出口及五工具验收仍未完成。
