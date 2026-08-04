# Changelog

## 0.6.0 (2026-08-04)

### 破坏性变更

- **`MCP_HOST` 默认改为 `127.0.0.1`**：HTTP 传输默认仅监听本地回环；对外暴露需显式设为 `0.0.0.0` 或具体 IP，并务必配置 `MCP_AUTH_TOKEN`
- **docker-compose 默认仅发布到宿主机回环**：ports 改为 `127.0.0.1:8080:8080`，`${MCP_PORT}` 不再影响宿主侧端口（容器内监听端口固定 8080）；对外暴露去掉 `127.0.0.1:` 前缀并配置 `MCP_AUTH_TOKEN`

### 新增

- **DNS 重绑定防护**：监听回环时自动启用 mcp SDK 内置 Host/Origin 校验（防浏览器重绑定攻击），sse / streamable-http 两种传输策略一致；对外暴露时显式关闭并打印安全告警，安全交由 Bearer 认证

### 文档

- README / `.env.example` 同步 `MCP_HOST` 默认值为 `127.0.0.1`；Stateless 示例移除无认证的 `0.0.0.0` 暴露写法

## 0.5.0 (2026-08-02)

### 破坏性变更

- **`VELA_READ_ONLY` 默认改为 `true`**：未显式配置时 7 个写工具不注册。需写能力请显式设为 `false`
- **写前确认迁移到 MCP 2.0 `Resolve` + `Elicit`**：删除旧的 `_confirm_action`（`ctx.elicit()` + fail-open），改用 SDK 原生机制。`confirm` 参数对 AI 不可见；客户端不支持 Elicitation 时 SDK 拒绝调用（`-32021`），不再降级执行
  - 新增确认：`deploy`（提示区分 force）、`resume_workflow`、`create_trigger`
  - 迁移确认：`rollback`、`terminate_workflow`
- **`force` 参数描述修正**：改为贴合 VelaUX 原义"忽略未完成的部署事件"
- **`handle_error` 补充 10004**：部署冲突时引导查工作流记录，避免 AI 自行加 force 重试

### 测试

- 新增 `write_mode` fixture 与 `test_default_is_read_only`
- 新增确认路径测试 9 条（accept/decline/cancel/confirm=False、参数不可见、拒绝不调用 VelaUX、10004）

## 0.4.0 (2026-07-31)

### 破坏性变更

- **`vela_velaql_query` 输入参数重构**：旧的自由字符串 `velaql: str` 改为 schema-typed `(view, params)`
  - `view` 现在是 9 选 1 的枚举（component-pod-view / collect-logs / service-view / 等）
  - `params` 是结构化 JSON 字典，由对应 view 的 Pydantic schema 校验
  - 已知 view 的参数错误以可解析的 markdown 文本返回，LLM 可直接重试

## 0.3.0 (2026-07-30)

### 新增

- **MCP 2.0 P2 改造**：完整迁移到 MCP SDK v2（`MCPServer`、`ToolAnnotations`、`Context`）
- **破坏性操作 MRTR 确认**：`vela_delete_application`、`vela_delete_component`、`vela_delete_env` 等高危操作通过 MCP 2.0 Elicitation 机制弹出确认表单；不支持 Elicitation 的客户端自动降级为直接执行
- **MCP Resources**：以 `kubevela://` URI 暴露系统信息、项目列表、环境列表、集群列表等只读元数据
- **Stateless HTTP 模式**：支持 `MCP_STATELESS_HTTP=true` 无状态部署
- **TLS 证书验证跳过**：新增 `VELA_INSECURE` 环境变量，用于自签名证书环境（开发/测试专用）

### 修复

- 确认机制改为 fail-closed（异常时阻断操作）
- 共享 `httpx.AsyncClient` 实例替代每次新建
- 结构化输出基类增加 `model_config = ConfigDict(extra="allow")`
- 修复 `ruff` E501 行长超限问题
- 统一 `handle_error` 注释描述
- 移除 KubeVela MRTR 双路径（`confirm` 参数），统一为 ctx 非空自动触发确认

### 文档

- README 补充 MRTR 确认、Resources、Stateless HTTP、TLS 跳过等章节
- `.env.example` 补充 `VELA_INSECURE`
