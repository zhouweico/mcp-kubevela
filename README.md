# mcp-kubevela

KubeVela MCP Server - 让 AI 助手能够查询和管理 [KubeVela](https://kubevela.io/) 的应用交付：查询应用状态、触发部署、跟踪工作流、管理插件等。

基于 VelaUX REST API（`/api/v1`，JWT Bearer 认证，参见 [VelaUX OpenAPI 文档](https://kubevela.io/docs/platform-engineers/openapi/overview/)），支持应用交付的查询与操作。

## 特性

- **多协议传输**：`stdio`（默认）、`sse`、`streamable-http`，一套代码适配本地与远程场景
- **接口认证**：HTTP 传输支持 Bearer Token 保护，未授权请求返回 `401`
- **JWT 自动管理**：用户名/密码登录换取 accessToken，`401` 时自动 refresh / 重登录并重放请求，无需手工维护 Token
- **KubeVela 原生概念**：直接以 `project / application / env / target / workflow / addon` 组织交付，读写一体
- **默认只读**：写工具默认不注册，需显式 `VELA_READ_ONLY=false` 开启，避免未经配置即直连生产环境
- **危险操作防护**：回滚/终止工作流等高危操作带 `destructiveHint` 注解并通过 MRTR 确认；删除应用、回收环境、禁用插件等高危能力**未提供工具**，从根源上杜绝误操作
- **破坏性操作 MRTR 确认**：回滚应用、终止工作流等高危操作通过 MCP 2.0 Elicitation 机制弹出确认表单，需用户明确同意后才执行；若客户端不支持 Elicitation（如 stdio 模式），则降级为直接执行
- **MCP Resources**：以 `vela://` URI 暴露系统信息、项目列表、环境列表、集群列表等只读元数据，客户端可直接读取
- **Stateless HTTP**：支持无状态 HTTP 模式，每次请求独立处理、无会话状态，适合 Serverless / 多副本部署
- **灵活部署**：`uvx` 免安装运行、Docker 构建即用

## 前置准备

准备一个可访问的 VelaUX（KubeVela 的 API Server + 控制台）实例。你需要准备：

- VelaUX 地址（如 `http://localhost:8000`）
- 登录用户名 / 密码（首次安装 VelaUX 后默认管理员为 `admin`）

## 快速开始

### MCP 客户端（stdio，本地）

以 Claude Code 为例，在项目 `.mcp.json` 或全局 `~/.claude.json` 中添加：

```json
{
  "mcpServers": {
    "kubevela": {
      "type": "stdio",
      "command": "uvx",
      "args": ["mcp-kubevela"],
      "env": {
        "VELA_URL": "http://localhost:8000",
        "VELA_USERNAME": "admin",
        "VELA_PASSWORD": "your-password",
        "VELA_READ_ONLY": "false"
      }
    }
  }
}
```

> 本服务**默认只读**（`VELA_READ_ONLY=true`），写工具不注册。上述示例显式设为 `false`
> 以启用创建 / 部署 / 回滚等写能力；仅需查询时删掉该项即可。

> Cursor、OpenCode、Claude Desktop 等客户端的配置格式相同，核心均为 `command: uvx` + `args: ["mcp-kubevela"]`，按各客户端语法填入 `VELA_*` 环境变量即可。

### Docker

**方式一：stdio（由客户端拉起容器，适合本地集成）**

```json
{
  "mcpServers": {
    "kubevela": {
      "type": "stdio",
      "command": "docker",
      "args": ["run", "-i", "--rm", "ghcr.io/zhouweico/mcp-kubevela:latest"],
      "env": {
        "VELA_URL": "http://your-velaux:8000",
        "VELA_USERNAME": "admin",
        "VELA_PASSWORD": "your-password"
      }
    }
  }
}
```

> 必须带 `-i`（保持 stdin 管道），否则容器内的 stdio 服务无法与客户端通信。

**方式二：HTTP + 认证（容器独立运行，客户端远程连接，适合多客户端共享）**

先启动容器：

```bash
docker run -d -p 8080:8080 \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_AUTH_TOKEN=your-strong-token \
  -e VELA_URL=http://your-velaux:8000 \
  -e VELA_USERNAME=admin \
  -e VELA_PASSWORD=your-password \
  ghcr.io/zhouweico/mcp-kubevela:latest
```

再在 Claude Code 的 `.mcp.json` 中通过 HTTP 连接：

```json
{
  "mcpServers": {
    "kubevela": {
      "type": "streamable-http",
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer your-strong-token"
      }
    }
  }
}
```

## 可用工具

### 只读工具（21 个）

按业务域分组排列：应用 → 部署与工作流 → 触发器 → 项目与环境 → 平台。

| 工具 | 分组 | 说明 | 对应 API |
|------|------|------|----------|
| `vela_list_applications` | 应用 | 应用列表（支持项目/环境/目标/关键字过滤） | `GET /applications` |
| `vela_get_application` | 应用 | 应用详情（基础信息、环境绑定、策略） | `GET /applications/{app}` |
| `vela_get_app_status` | 应用 | 运行状态（全环境概览或单环境详情） | `GET .../status` |
| `vela_list_components` | 应用 | 组件列表 / 组件详情（含 properties/traits） | `GET .../components[/{comp}]` |
| `vela_list_revisions` | 应用 | 版本历史（可按环境/状态过滤） | `GET .../revisions` |
| `vela_compare_application` | 应用 | 配置差异对比（最新配置 vs 运行态 / 指定版本 vs 运行态或最新） | `POST .../compare` |
| `vela_get_application_manifest` | 应用 | 导出 Application CR YAML（GitOps 迁移 / 备份） | `POST .../compare` |
| `vela_list_deploy_records` | 部署与工作流 | 环境部署记录 | `GET .../envs/{env}/records` |
| `vela_list_workflow_records` | 部署与工作流 | 工作流列表 / 执行记录 / 记录详情（三合一） | `GET .../workflows[...]` |
| `vela_get_workflow_logs` | 部署与工作流 | 工作流步骤日志 | `GET .../records/{r}/logs` |
| `vela_list_triggers` | 触发器 | Webhook 触发器列表（含 token 与触发地址） | `GET .../triggers` |
| `vela_list_projects` | 项目与环境 | 项目列表（名称 / 别名 / 命名空间 / 负责人） | `GET /projects` |
| `vela_list_project_targets` | 项目与环境 | 项目可用的交付目标 | `GET /projects/{p}/targets` |
| `vela_list_project_users` | 项目与环境 | 项目成员及角色（权限排查） | `GET /projects/{p}/users` |
| `vela_list_envs` | 项目与环境 | 环境列表（可按项目过滤） | `GET /envs` |
| `vela_list_targets` | 项目与环境 | 交付目标列表 | `GET /targets` |
| `vela_list_clusters` | 平台 | 集群列表 / 集群详情 | `GET /clusters[/{c}]` |
| `vela_list_addons` | 平台 | 插件市场 / 已启用插件 / 详情+状态 | `GET /addons[...]` |
| `vela_list_definitions` | 平台 | X-Definition 列表 / 参数 schema | `GET /definitions[...]` |
| `vela_velaql_query` | 平台 | VelaQL 查询（Pod / 日志 / 资源拓扑），schema-typed `(view, params)` 接口覆盖 9 个已知 view | `GET /query` |
| `vela_system_info` | 平台 | 平台系统信息（版本 / 登录方式 / 集群与应用统计 / 已启用插件） | `GET /system_info` |

### 写工具（7 个）

按交付生命周期排列：创建 → 预演 → 部署 → 工作流控制 → 回滚 → 触发器。

| 工具 | 分组 | 说明 | 对应 API |
|------|------|------|----------|
| `vela_create_application` | 应用 | 创建应用（含首个组件） | `POST /applications` |
| `vela_dry_run_application` | 应用 | 部署预演（只渲染不落地） | `POST .../dry-run` |
| `vela_deploy_application` | 应用 | 触发部署（异步，返回部署记录） | `POST .../deploy` |
| `vela_resume_workflow` | 部署与工作流 | 恢复挂起的工作流（审批放行） | `.../resume` |
| `vela_terminate_workflow` | 部署与工作流 | 终止执行中的工作流（MRTR 确认） | `.../terminate` |
| `vela_rollback_application` | 应用 | 回滚到指定版本（MRTR 确认） | `.../rollback` |
| `vela_create_trigger` | 触发器 | 创建 Webhook 触发器（返回 token 与触发地址） | `POST .../triggers` |

> **未提供的高危操作**：删除应用、回收环境、启用/禁用插件、删除触发器**未实现为工具**，
> 此类操作请通过 VelaUX 控制台或 `vela` CLI 人工执行。
>
> **只读 / 写的区别**：「类型 = 只读」的 21 个工具在只读模式下**仍然可用**；
> 「类型 = 写」的 7 个工具会被**完全排除**——不出现在 `tools/list` 中，
> Agent 既看不到也无法调用（注册期排除，非运行期拦截）。
> **只读为默认行为**，因此未显式设置 `VELA_READ_ONLY=false` 的部署，Agent 只能查询、
> 绝无意外变更交付的风险。
>
> 部署为**异步语义**：`vela_deploy_application` 触发后立即返回部署记录标识，
> 用 `vela_list_workflow_records` / `vela_get_workflow_logs` 轮询进度与日志。

## 配置

### 环境变量

**MCP 传输与认证**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MCP_TRANSPORT` | 传输协议：`stdio` / `sse` / `streamable-http` | `stdio` |
| `MCP_HOST` | HTTP 传输监听地址（stdio 忽略） | `0.0.0.0` |
| `MCP_PORT` | HTTP 传输监听端口（stdio 忽略） | `8080` |
| `MCP_AUTH_TOKEN` | 设置后启用 Bearer Token 认证，保护 HTTP 接口 | -（不鉴权） |
| `MCP_STATELESS_HTTP` | 启用无状态 HTTP 模式，适合 Serverless 部署（详见下方说明） | `false` |
| `MCP_LOG_LEVEL` | 日志级别：`debug`/`info`/`warning`/`error` | `info` |

**VelaUX 连接**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `VELA_URL` | VelaUX API Server 地址 | `http://localhost:8000` |
| `VELA_USERNAME` | 登录用户名（必填） | - |
| `VELA_PASSWORD` | 登录密码（必填） | - |
| `VELA_TIMEOUT` | 请求超时（秒） | `30` |
| `VELA_READ_ONLY` | 只读模式，排除全部写工具。**默认只读**，需写能力时显式设为 `false` | `true` |
| `VELA_INSECURE` | 跳过 TLS 证书验证，用于自签名证书环境（详见下方说明） | `false` |

> 认证凭证只需用户名/密码：客户端首次请求时自动调用 `POST /api/v1/auth/login`
> 换取 accessToken / refreshToken 并缓存；收到 `401` 时先尝试 refresh 续期、
> 失败则重新登录，然后重放原请求（最多一次），全程无需人工干预。

### KubeVela 概念说明

KubeVela 的交付组织层级为：**项目（project）> 应用（application）> 环境绑定（env binding）> 组件（component）/ 运维特征（trait）**，部署由**工作流（workflow）**驱动，落点为**交付目标（target，对应集群+命名空间）**。

- 组件的 `properties` 传参为 **JSON 字符串**（如 `'{"image":"nginx:latest"}'`），由组件定义（ComponentDefinition）的 schema 约束，可用 `vela_list_definitions` 查询参数 schema。
- 部署后应用状态、Pod、日志等运行时信息可通过 `vela_get_app_status` 与 `vela_velaql_query` 获取。

### 只读模式

**本服务默认即只读模式**（`VELA_READ_ONLY=true`），全部写工具不注册，仅允许查询，可直接用于生产环境。

如需写能力（创建 / 部署 / 回滚 / 终止工作流 / 创建触发器），须显式关闭只读：

```json
{
  "env": {
    "VELA_READ_ONLY": "false"
  }
}
```

### TLS 证书验证

本服务基于 httpx2 发起 HTTPS 请求，**默认会验证 TLS 证书**（行为与 httpx 一致）。

- 在使用自签名证书或内部 CA 的环境中，HTTPS 请求会因证书校验失败而报错。此时可设置环境变量 `VELA_INSECURE=true` 跳过 TLS 证书验证。
- 该选项适用于开发、测试等使用自签名证书的环境。

```json
{
  "env": {
    "VELA_INSECURE": "true"
  }
}
```

> **安全警告**：禁用 TLS 证书验证是不安全的，会使得 HTTPS 连接容易受到中间人攻击。**请勿在生产环境中使用**，生产环境应使用受信任的 CA 签发的有效证书。

## 多协议传输

通过 `MCP_TRANSPORT` 选择传输协议：

- **`stdio`（默认）**：标准输入输出，适合 Claude Code、Cursor 等本地 AI 客户端集成。
- **`sse`**：Server-Sent Events，HTTP 传输，端点 `http://<host>:<port>/sse`。
- **`streamable-http`**：Streamable HTTP，端点 `http://<host>:<port>/mcp`。

以 `streamable-http` 启动示例：

```bash
MCP_TRANSPORT=streamable-http \
MCP_HOST=0.0.0.0 MCP_PORT=8080 \
MCP_AUTH_TOKEN=your-strong-token \
mcp-kubevela
```

## 接口认证

设置 `MCP_AUTH_TOKEN` 后，所有 HTTP 请求必须携带正确 Token，否则返回 `401`：

```
Authorization: Bearer <MCP_AUTH_TOKEN>
```

也兼容 `X-Auth-Token` / `X-MCP-Token` 请求头。健康检查端点 `GET /health` 免鉴权，返回 `{"status":"ok"}`，用于容器探活。

> `stdio` 传输为本地进程通信，不涉及网络，无需也不会进行 Token 认证。未设置 `MCP_AUTH_TOKEN` 时 HTTP 接口不鉴权，生产环境请务必配置。
>
> 注意区分两类凭证：`MCP_AUTH_TOKEN` 保护本 MCP Server 的 HTTP 接口；`VELA_USERNAME` / `VELA_PASSWORD` 用于登录 VelaUX API，两者互不相关。

## MCP Resources

本服务以 MCP 2.0 Resources 暴露只读元数据，客户端可直接通过 URI 读取，无需调用工具：

| Resource URI | 说明 |
|---|---|
| `vela://system-info` | VelaUX 平台系统信息（版本 / 登录方式 / 集群与应用统计 / 已启用插件） |
| `vela://projects` | 列出所有项目 |
| `vela://envs` | 列出所有环境 |
| `vela://clusters` | 列出所有纳管集群 |

> Resources 仅暴露只读数据，不涉及任何写操作。

## Stateless HTTP 模式

设置 `MCP_STATELESS_HTTP=true` 可启用无状态 HTTP 模式，每次请求独立处理、不保留会话状态，适合 Serverless 平台（如 AWS Lambda、阿里云函数计算）或多副本无状态部署：

```bash
MCP_TRANSPORT=streamable-http \
MCP_STATELESS_HTTP=true \
MCP_HOST=0.0.0.0 MCP_PORT=8080 \
mcp-kubevela
```

> Stateless 模式下不支持流式响应（SSE stream），每个 HTTP 请求独立完成工具调用后返回。适合短时、无状态的工具调用场景。

## 权限模型

本 MCP Server **不实现任何鉴权逻辑**：它仅用配置的 `VELA_USERNAME` / `VELA_PASSWORD` 登录 VelaUX，原样转发请求。
因此，**你能看到哪些数据、能执行哪些写操作，完全由该 VelaUX 账号在 KubeVela 的「项目角色」决定**，与 MCP Server 本身无关。

- **数据权限**：账号只能查询其项目角色覆盖范围内的项目 / 应用 / 环境 / 交付目标。例如列全量应用时，返回的正是该账号有权看到的子集，并非平台全量。
- **执行权限**：触发部署、恢复/终止工作流、回滚、创建触发器等写操作能否成功，取决于账号在对应项目是否拥有足够角色（如 `project-admin` / `project-edit` / 自定义角色）。若 VelaUX 返回 `403`，说明账号权限不足——这是 KubeVela 的授权结果，不是 MCP 的限制。
- **`VELA_READ_ONLY` 不是权限开关**：它只在 MCP 层决定「是否注册写工具」（粗粒度安全闸），既不会授予、也不会剥夺任何 VelaUX 权限。真正的授权始终来自登录账号本身。
- **最小权限建议**：生产环境建议为 MCP 配置**专用 VelaUX 账号**，仅授予所需项目的最小角色（如 `project-view` / `project-edit`），而非平台管理员。多租户隔离应通过 VelaUX 的项目角色实现，而非依赖本服务的配置项。

> 不确定当前账号在某个项目的角色？用 `vela_list_project_users`（指定 `project_name`）查看该项目的成员与角色即可排查权限问题。

## 容器化部署

### 本地构建（Docker）

```bash
# 构建镜像
docker build -t mcp-kubevela:latest .

# 以 streamable-http 运行并启用认证
docker run -d --name mcp-kubevela -p 8080:8080 \
  -e MCP_TRANSPORT=streamable-http \
  -e MCP_AUTH_TOKEN=your-strong-token \
  -e VELA_URL=http://your-velaux:8000 \
  -e VELA_USERNAME=admin \
  -e VELA_PASSWORD=your-password \
  mcp-kubevela:latest
```

### Docker Compose

复制 `.env.example` 为 `.env` 并按需修改，然后：

```bash
cp .env.example .env
docker compose up -d
```

`docker-compose.yml` 已内置 `build`（基于本地 `Dockerfile` 构建并标记为 `mcp-kubevela:latest`）和健康检查（探测 `/health`），以非 root 用户运行，适合本地开发部署。

## 使用场景示例

配置好后，你可以这样和 AI 对话（每条示例后括注主要涉及的工具）：

### 平台初识与巡检

新接入一个环境，先摸清平台全貌：

```
连一下 KubeVela 平台，告诉我版本、有几个集群、几个应用、开了哪些插件
```
（`vela_system_info`）

```
列出所有项目和各自的负责人，再看看 default 项目能部署到哪些交付目标
```
（`vela_list_projects` + `vela_list_project_targets`）

```
盘点一下所有集群和环境，画一张「项目 → 环境 → 目标集群」的映射表
```
（`vela_list_clusters` + `vela_list_envs` + `vela_list_targets`）

```
现在启用了哪些插件？fluxcd 插件的状态如何，插件市场里有没有可用的更新版本
```
（`vela_list_addons`）

### 查询交付

```
列出 default 项目下的所有应用，按环境分组
```
（`vela_list_applications`）

```
demo 应用在 prod 环境的运行状态怎么样？有没有异常的组件
```
（`vela_get_app_status`）

```
看看 demo 应用有哪些组件，webservice 组件的镜像和资源配置是什么
```
（`vela_list_components`）

```
webservice 这种组件类型都支持哪些参数？我想加个环境变量
```
（`vela_list_definitions`）

### 部署与发布

```
创建一个应用 my-app，组件用 webservice，镜像 nginx:latest，部署到 dev 环境
```
（`vela_list_definitions` → `vela_create_application`）

```
先 dry-run 看看渲染结果有没有问题，没问题再把 demo 部署到 prod，然后盯着进度直到完成
```
（`vela_dry_run_application` → `vela_deploy_application` → `vela_list_workflow_records` 轮询）

```
部署卡在人工审批了，帮我放行
```
（`vela_list_workflow_records` → `vela_resume_workflow`）

```
这次发布不对劲，先终止工作流，看下版本历史，回滚到上一个正常版本
```
（`vela_terminate_workflow` → `vela_list_revisions` → `vela_rollback_application`）

### 故障排查

```
demo 最近一次部署的工作流执行到哪一步了？把失败步骤的日志给我
```
（`vela_list_workflow_records` + `vela_get_workflow_logs`）

```
用 VelaQL 查一下 demo 在 prod 环境的 Pod 列表，有没有在重启的
```
（`vela_velaql_query`，`view=component-pod-view`，`params={appNs, appName}`）

```
demo 应用线上行为和配置对不上，帮我对比一下集群运行态和最新配置有没有漂移
```
（`vela_compare_application`）

```
对比一下 demo 当前运行态和 v2 版本的配置差异，看看当时改了什么
```
（`vela_list_revisions` + `vela_compare_application`）

```
prod 环境最近的部署记录列一下，找找是哪次部署之后开始出问题的
```
（`vela_list_deploy_records`）

### GitOps 与备份

```
把 demo 应用的最新配置导出成 Application YAML，我要提交到 Git 仓库
```
（`vela_get_application_manifest`，`source=latest`）

```
导出 demo 当前在集群里实际运行的 YAML，和 Git 里的版本对比一下
```
（`vela_get_application_manifest`，`source=running`）

### CI/CD 集成

```
给 demo 应用建一个 webhook 触发器，Harbor 推送镜像后自动部署到 dev 环境，把触发地址给我
```
（`vela_create_trigger`，`payloadType=harbor`）

```
demo 现在有哪些触发器？把每个的触发地址和绑定的工作流列出来
```
（`vela_list_triggers`）

### 权限与协作排查

```
我部署 demo 到 prod 报 403，看看我在这个项目里是什么角色
```
（`vela_list_project_users`）

```
prod-cluster 这个目标在哪些项目里可用？帮我确认 team-a 项目能不能部过去
```
（`vela_list_projects` + `vela_list_project_targets`）

> 回滚 / 终止工作流等危险操作通过 MRTR 确认机制要求用户二次确认，避免对话中的误操作直接落到集群。
> 删除应用、回收环境、启用/禁用插件等高危操作未提供工具，请在 VelaUX 控制台或 `vela` CLI 中人工执行。
>
> **MRTR 确认**：`vela_rollback_application` 和 `vela_terminate_workflow` 执行前会通过 MCP 2.0 Elicitation 弹出确认表单，需用户明确同意后才执行。若客户端不支持 Elicitation（如 stdio 模式），则降级为直接执行。

## 开发

```bash
pip install -e ".[dev]"
pytest          # respx mock 测试，无需真实 VelaUX 环境
ruff check src tests
```

### 实现注记

- VelaUX 工作流记录的 `resume` / `terminate` / `rollback` 接口是 **GET** 方法（非 POST），客户端已按源码契约实现
- 组件 `properties` 是 JSON **字符串**（非对象）
- 应用列表接口无分页参数；其余列表接口统一 `page` / `pageSize`
- 错误响应结构为 `{"BusinessCode": int, "Message": str}`

## License

MIT
