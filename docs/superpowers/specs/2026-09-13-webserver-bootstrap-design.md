# Smart Docs Platform WebServer 基线迁入设计

## 目标

将 `markparticle/WebServer` 的当前 `master` 分支作为 Smart Docs Platform 的 C++ WebServer 基线，以源码快照方式迁入当前仓库，建立独立、清晰的新 Git 历史，并重写项目 README。

本次工作只建立后续开发基线，不实现需求文档中尚未完成的文件管理、文档检索、MCP 或 Agent 功能，也不将规划功能描述成已有成果。

## 迁入方式

- 从 `https://github.com/markparticle/WebServer.git` 获取当前 `master` 快照。
- 不复制上游 `.git` 目录，不保留上游提交历史。
- 将上游源码、测试、静态资源、构建文件和 Apache-2.0 `LICENSE` 放入当前仓库。
- 保留根目录的 `团队文档管理与智能整理平台_需求文档.md`。
- 使用当前仓库自己的 `main` 分支和新的提交历史。
- 将远程 `origin` 设置为 `https://github.com/NoUmbrellaK/Smart-Docs-Platform.git`。

## 开源归属与修改边界

上游 WebServer 使用 Apache License 2.0。本仓库保留完整 `LICENSE`，并在 README 的“开源来源”章节明确以下信息：

- C++ WebServer 基线来源及链接；
- 本仓库通过源码快照迁入，而非上游官方发行版；
- 当前提交主要完成项目重命名、文档整理与 Git 基线建立；
- 后续修改由本仓库独立维护。

本次不改写上游 C++ 业务代码，因此无需在源码文件内增加修改声明。后续修改继承文件时，应按 Apache-2.0 要求保留适用归属并标示修改。

## README 结构

新的 `README.md` 使用中文，包含：

1. Smart Docs Platform 的项目定位；
2. “当前已具备”与“规划实现”的明确分界；
3. 当前 WebServer 的 Epoll、Reactor、线程池、HTTP、定时器、日志和 MySQL 能力；
4. 需求文档规划的 C++、Python MCP/Agent、索引和前端组件关系；
5. 当前目录结构；
6. Linux、C++14、Make 和 MySQL 开发依赖；
7. 构建、配置和运行命令；
8. M1 至 M5 开发路线；
9. 安全提示、贡献说明、许可证与上游归属。

README 不复用上游未经本仓库验证的性能数据，也不声称需求文档中的验收目标已经完成。

## Git 历史

Git 历史按两个可审查节点组织：

1. 保存原始需求与本设计，建立 Smart Docs Platform 的决策基线；
2. 导入 WebServer 快照并重写 README，形成可构建的代码基线。

最终分支为 `main`。推送前确认目标远程仍为空或不存在会被覆盖的分支；若远程状态发生变化，则停止推送并报告冲突，不使用强制推送。

## 验证

迁入后执行以下检查：

- 检查上游 `.git` 未被带入；
- 检查 `LICENSE` 和 README 上游归属存在；
- 检查 README 不包含已实现 Smart Docs 功能的失实描述；
- 使用仓库原生 Makefile 构建；
- 若环境缺少 MySQL 客户端开发库，记录依赖缺失，并至少完成源码与构建配置静态检查；
- 检查 Git 工作区干净、分支为 `main`、远程地址正确；
- 使用普通 `git push -u origin main` 推送，不强制覆盖。

## 非目标

本次不新增依赖，不调整 WebServer 架构，不实现 Smart Docs API，不接入数据库迁移、Python MCP 服务、Agent、向量检索、前端页面、容器部署或 CI。
