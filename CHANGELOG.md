# Changelog

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
