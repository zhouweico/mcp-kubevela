# `vela_velaql_query` 工具稳定性 — 设计

**日期**: 2026-07-31
**状态**: 待用户审核
**目标**: 让 AI 模型稳定、正确地使用 `vela_velaql_query` 工具 (语法/参数正确性维度)

---

## 1. 问题陈述

当前 `vela_velaql_query` (定义于 `src/mcp_kubevela/server.py:801-822`) 的接口:

```python
async def vela_velaql_query(velaql: Annotated[str, Field(...)]) -> str
```

接受**单一自由字符串**, 对应的 VelaUX API `GET /api/v1/query?velaql=<raw>` 也是黑盒透传。
这导致 LLM 在使用时频繁出错:

1. **视图名拼错**: 7 个常用 view 中 (见 §3), LLM 经常臆造不存在名 (如 `pod-list-view`)
2. **参数语义混淆**: `appNs` (KubeVela 项目命名空间) 与 `namespace` (K8s 真实命名空间) 拼写接近, 语义不同 — LLM 容易混用
3. **必填/可选参数混淆**: `collect-logs` 有 4 必填 + 3 可选, LLM 经常漏掉 `container` 或 `tailLines`
4. **错误不可恢复**: VelaUX 返回 4xx 时仅透传原文, LLM 无法判断是视图名错、参数错、还是权限问题
5. **工具描述信息量低**: docstring 仅 4 行 + 2 个示例, 缺少结构化视图目录

## 2. 目标

| 维度 | 现状 | 目标 |
|---|---|---|
| LLM 拼对视图名 | 靠记忆 + 2 个示例 | 枚举限定, 拼写错误客户端即拒 |
| LLM 拼对参数 | 靠模板字符串组合 | JSON 字典 + Pydantic schema 校验 |
| 参数语义混淆 | 无提示 | 每个字段 description 明确写清"K8s ns" vs "KubeVela appNs" |
| 错误可恢复性 | traceback | 结构化错误: 缺什么、错什么、合法值是什么 |
| 向后兼容 | — | **不保留 raw 逃生口** (见 §6) |

**非目标**:
- 不改 VelaUX 后端, 不改 `client.velaql_query()` 实现
- 不引入新 view (本次只覆盖已观测到的 7 个)
- 不改其他 27 个 MCP 工具

## 3. 视图清单 (本次覆盖)

从生产环境真实抓取的 7 类 view 调用样本中提取, 分 3 个语义层:

| # | view | 层 | 必填参数 | 可选参数 (默认) |
|---|---|---|---|---|
| 1 | `service-endpoints-view` | 应用 | `appNs, appName` | — |
| 2 | `application-resource-tree-view` | 应用 | `appNs, appName` | — |
| 3 | `service-applied-resources-view` | 应用 | `appNs, appName` | — |
| 4 | `component-pod-view` | 应用 | `appNs, appName` | — |
| 5 | `pod-view` | 资源 | `cluster, namespace, name` | — |
| 6 | `collect-logs` | 运维 | `cluster, namespace, pod, container` | `previous(false)`, `timestamps(true)`, `tailLines(3000)` |
| 7 | `application-resource-detail-view` | 资源 | `cluster, namespace, name, kind, apiVersion` | — |

`appNs` ≠ `namespace`: 前者是 KubeVela 项目命名空间 (VelaUX 项目列表中的 namespace), 后者是 K8s 真实命名空间。二者在多数场景下字符串相同, 但语义不同, 字段 description 必须显式区分。

**`application-resource-detail-view` 备注**: `kind` 与 `apiVersion` 必须配对 (例: Pod↔v1, Deployment↔apps/v1, Application↔core.oam.dev/v1beta1, HelmRelease↔helm.toolkit.fluxcd.io/v2beta1, ReplicaSet↔apps/v1)。配对错误会由 VelaUX 端拒绝。本次**不在客户端强制配对** (合法组合过多, 维护成本高), 仅在字段 description 中提示。

## 4. 架构

新增模块 `src/mcp_kubevela/velaql/`, 仅做数据/纯函数, 不发起 I/O:

```
src/mcp_kubevela/
  velaql/
    __init__.py            # 对外暴露 VIEWS, compile, VelaQLView
    views.py               # VelaQLView 枚举 + 4 个 ParamSchema (BaseModel) + ViewSpec 注册表
    compiler.py            # 纯函数: (view, params: dict) -> velaql 字符串
    errors.py              # VelaQLError / VelaQLUnknownViewError / VelaQLParamError
  server.py                # 改造 vela_velaql_query tool, 调用 compiler
  clients/velaux.py        # 不变
tests/
  test_velaql_compiler.py  # 纯函数单测
  test_server.py           # 补 tool-level 测试
```

**职责边界**:
- `views.py`: 仅数据 (Enum + BaseModel + 字典), 零 I/O
- `compiler.py`: 纯函数 `(view: VelaQLView, params: dict) -> str`, 单测覆盖
- `errors.py`: typed exceptions, 工具内捕获后转为 markdown
- `server.py`: 取 view → 校验 params → compile → 调 `client.velaql_query()` → 渲染

## 5. 数据流

### Happy path

```
LLM tool call
  view=VelaQLView.COMPONENT_POD_VIEW, params={appNs:"devops-admin-test", appName:"xdevops-ui"}
        │
        ▼
server.vela_velaql_query()
        │
        ├─ 1. 查 VIEWS[view] -> ViewSpec(param_schema=ComponentPodViewParams, ...)
        ├─ 2. ComponentPodViewParams(**params) — Pydantic 校验
        ├─ 3. compiler.compile(view, params.model_dump())
        │     -> "component-pod-view{appNs=devops-admin-test,appName=xdevops-ui}"
        ├─ 4. client.velaql_query(velaql) -> dict
        └─ 5. render_kv("VelaQL 查询结果", data) -> markdown
```

### Error path

| 失败点 | 检测 | 返回给 LLM |
|---|---|---|
| `view` 不在枚举 | FastMCP 自动校验 | "Unknown view 'foo-bar'. Valid: [service-endpoints-view, ...]" |
| 缺必填参数 | Pydantic `Field(...)` | "Missing required params: appName. View 'component-pod-view' requires: appNs, appName." |
| 参数类型错 | Pydantic | "param 'tailLines' must be int, got str" |
| 参数枚举值错 | Pydantic `Literal` | "param 'previous' must be one of [true, false], got 'yes'" |
| 服务端 4xx 透传 | catch in tool | "[VelaUX 400] unknown view — check velaql/views" |

**所有错误都返回结构化 markdown, LLM 可直接 parse 并重试。**

## 6. 输入 schema (Pydantic)

```python
class VelaQLView(str, Enum):
    SERVICE_ENDPOINTS_VIEW             = "service-endpoints-view"
    APPLICATION_RESOURCE_TREE_VIEW     = "application-resource-tree-view"
    SERVICE_APPLIED_RESOURCES_VIEW     = "service-applied-resources-view"
    COMPONENT_POD_VIEW                 = "component-pod-view"
    POD_VIEW                           = "pod-view"
    APPLICATION_RESOURCE_DETAIL_VIEW   = "application-resource-detail-view"
    COLLECT_LOGS                       = "collect-logs"

# === 应用层 (4 个 view 共用同一 ParamSchema) ===
class _AppLayerBase(BaseModel):
    appNs:   str = Field(..., description="KubeVela 项目命名空间 (VelaUX 项目列表中的 namespace, 通常与 K8s ns 同名)")
    appName: str = Field(..., description="应用名 (vela_list_applications 返回的 name 字段)")

# === 资源层 ===
class PodViewParams(BaseModel):
    cluster:   str = Field(..., description="目标集群名 (vela_list_clusters 返回的 name)")
    namespace: str = Field(..., description="K8s 命名空间 — 注意: 这里是 K8s 真实命名空间, 不是 KubeVela appNs")
    name:      str = Field(..., description="Pod 名 (含 deployment 哈希后缀, 如 xdevops-ui-5ffb6c4b-wxb54)")

class ResourceDetailViewParams(BaseModel):
    """application-resource-detail-view{...}

    查询某个 K8s 资源的详情, 通过 kind + apiVersion 唯一确定 GVR。
    kind / apiVersion 必须配对 (Pod↔v1, Deployment↔apps/v1, Application↔core.oam.dev/v1beta1 等),
    配对错误会由 VelaUX 端拒绝。
    """
    cluster:    str = Field(..., description="目标集群名")
    namespace:  str = Field(..., description="K8s 命名空间")
    name:       str = Field(..., description="资源名 (Pod/Deployment/etc. 的 metadata.name)")
    kind:       str = Field(..., description="K8s 资源 Kind, 如 Pod / Deployment / ReplicaSet / Application / HelmRelease")
    apiVersion: str = Field(..., description="K8s apiVersion, 如 v1 / apps/v1 / core.oam.dev/v1beta1 / helm.toolkit.fluxcd.io/v2beta1")

# === 运维层 ===
class CollectLogsParams(BaseModel):
    cluster:    str  = Field(..., description="目标集群名")
    namespace:  str  = Field(..., description="K8s 命名空间")
    pod:        str  = Field(..., description="Pod 名")
    container:  str  = Field(..., description="容器名 (Pod 内多容器时必填)")
    previous:   bool = Field(default=False, description="true=取重启前日志; false=取当前日志")
    timestamps: bool = Field(default=True,  description="是否在每行加时间戳")
    tailLines:  int  = Field(default=3000,  description="尾部行数", ge=1, le=10000)
```

**Tool 顶层签名**:

```python
@mcp.tool(name="vela_velaql_query", annotations=_ro("VelaQL 查询"))
async def vela_velaql_query(
    view: Annotated[VelaQLView, Field(description="视图。必须是受支持 view 之一。")],
    params: Annotated[dict[str, Any], Field(
        description=(
            "视图参数 (JSON 对象)。键名见 view 描述: "
            "应用层 view -> {appNs, appName}; "
            "pod-view -> {cluster, namespace, name}; "
            "application-resource-detail-view -> {cluster, namespace, name, kind, apiVersion}; "
            "collect-logs -> {cluster, namespace, pod, container, [previous, timestamps, tailLines]}"
        )
    )],
    cluster: Annotated[str | None, Field(
        description=("多集群覆盖; 应用层 view 忽略此参数 (应用通过 target 决定集群), "
                     "pod-view / collect-logs 此参数为必填 (在 params 之外, 此处供未来 cluster 路由优化)"),
        default=None,
    )] = None,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    ...
```

> **关于顶层 `cluster`**: 当前为预留, 实际编译时仍以 `params.cluster` 为准 (因为 `params` 已校验必填)。保留顶层 slot 是为了未来"多集群并行查询"等增强, 不破坏 schema。本次**不会**实现并行查询逻辑, 仅留位。

## 7. 关键设计决策

### D1: 不保留 raw mode 逃生口

- 理由: 逃生口让 LLM 重新回到"乱写字符串"模式, 与目标直接冲突
- 备选: 若实际有用户需要, 后续可加 `mode: Literal["structured", "raw"] = "structured"` — 但本次不做

### D2: `cluster` 放在 tool 顶层而非 view schema 内

- 当前实现: `cluster` 既是 tool 顶层参数 (默认 None), 也是部分 view 的 `params` 必填项
- 行为: 应用层 view 忽略顶层 `cluster`; 资源/运维层 view 的 cluster 来自 `params.cluster` (Pydantic 已在 params 内强制必填)
- 顶层 `cluster` 留位是为了未来"同一查询跨多集群"等增强, 不破坏 schema
- **用户拍板**: 上次会话确认顶层放, 不污染 view schema
- **本次实施范围**: 顶层 `cluster` 仅作透传 placeholder, 实际查询以 `params.cluster` 为准

### D3: Pydantic `Literal` 不限制 `appNs` / `appName` 等自由字符串

- 这些值是从 VelaUX API 动态获取的 (项目名、应用名、Pod 名), 无法穷举
- 用 `str = Field(...)` 即可, 错误只在校验阶段由 VelaUX 端返回

### D4: 不引入 view 参数的"自动补全/联想"

- MCP 工具描述有 token 上限, 不可能塞下所有合法 `appNs` / `appName`
- 正确做法: 文档说清"先调 `vela_list_applications` 获取 appName 再来查"
- 这与 README 中已有的 "用 `vela_list_applications` 查 appName" 实践一致

## 8. 测试策略

### 8.1 纯函数测试 (`tests/test_velaql_compiler.py`)

- 每个 view 一个 round-trip: 已知 params → 期望 velaql 字符串
- 边界: `collect-logs` 三个可选参数默认值
- 错误路径: 缺必填、类型错、未知枚举值 (但这些实际由 Pydantic 拦截, 编译器只测 happy path)

### 8.2 Tool-level 测试 (`tests/test_server.py`)

- 用现有 respx mock 模式, mock `client.velaql_query` 返回固定 JSON
- 断言: 传入正确 view+params, 内部调用 `velaql_query("component-pod-view{...}")`
- 断言: 传入缺 `appName`, 返回结构化错误, 不调 `velaql_query`

### 8.3 兼容性测试

- 现有 `test_server.py:23` 提到 `vela_velaql_query` 名字, 改造后名字不变 — 通过
- 现有 1 个测试 (如果有) 调用旧 `(velaql: str)` — **预期会失败**, 需要在 PR 中更新

## 9. 迁移与回滚

- **破坏性变更**: 工具的入参从 `(velaql: str)` 改为 `(view, params, cluster, response_format)`
- 任何已有调用方需同步更新 (基于 README 中"用 VelaQL 查询 Pod"那 1 行示例)
- 回滚: `git revert` 单 commit 即可, 不涉及数据迁移

## 10. 实施顺序

1. 新建 `velaql/views.py` — Enum + 4 个 ParamSchema (覆盖 7 个 view) + VIEWS 注册表
2. 新建 `velaql/compiler.py` — 纯函数 + 单测
3. 新建 `velaql/errors.py` — typed exceptions
4. 改 `server.py:801-822` 的 tool 定义, 调用 compiler
5. 改 `tests/test_server.py` 中相关测试
6. 更新 README 中"故障排查"一节对 `vela_velaql_query` 的描述 (给 7 个 view 的示例)

## 11. 风险

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 新枚举值漏掉实际生产 view | 中 | LLM 仍能走错误信息定位 | 不保留 raw 模式, 报错清晰; 后续按需加 view |
| `appNs`/`namespace` 描述仍然让 LLM 混淆 | 低 | 仍可能调用错 view | description 显式 + 错误信息"view X 需要 cluster/namespace, 不是 appNs" |
| Pydantic 校验在 FastMCP 层的报错格式不可控 | 低 | 错误信息可能丑 | 在 tool 内显式 try/except, 重写错误 |
| 现有用户脚本破坏 | 中 | 旧调用需更新 | README 显式标注 breaking change; semver bump minor |

## 12. 范围外 (明确不做)

- 不为 `appNs` / `appName` 等做枚举值发现
- 不实现多集群并行查询 (顶层 `cluster` 仅留位)
- 不实现查询结果缓存
- 不实现 query result 分页 (VelaUX 端若有则透传, 没有则不处理)
- 不改其他 MCP 工具
- 不改 VelaUX 后端

---

**审核点**:
1. §3 视图清单 (7 个) 是否完整
2. §6 Pydantic schema 是否需要补 `description` 字段
3. §10 实施顺序是否合理
4. §1 问题陈述是否覆盖你实际遇到的痛点
