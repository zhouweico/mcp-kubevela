# `vela_velaql_query` 工具稳定性 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-form `velaql: str` parameter of the `vela_velaql_query` MCP tool with a schema-typed `(view, params)` interface covering 9 known views, so AI models stop making typos and parameter-confusion errors.

**Architecture:** New `src/mcp_kubevela/velaql/` package containing data (Enum + 6 Pydantic BaseModels) and a pure compiler. The `vela_velaql_query` tool in `server.py` validates the `view` enum, validates `params` against the view's BaseModel (catching Pydantic `ValidationError` to produce a structured error message), calls the compiler, then delegates to the existing `client.velaql_query()`. No backend / client changes.

**Tech Stack:** Python 3.10+, Pydantic v2 (>=2.0.0), FastMCP 2.0 (`@mcp.tool` with `Annotated[X, Field(...)]`), pytest + respx (existing dev stack).

---

## Global Constraints

- Python `>=3.10`; project uses `pyproject.toml` with `requires-python = ">=3.10"` and `target-version = "py310"`.
- `pydantic>=2.0.0`: use `Annotated[X, Field(...)]` style; use `model_config = ConfigDict(extra='forbid')` on ParamSchemas to catch unknown keys.
- `mcp>=2.0.0,<3.0.0`: tool signature must use `Annotated[..., Field(...)]` flat params, no `params` wrapper object as a tool arg. (Internal Pydantic validation inside the tool body is fine.)
- `ruff`: line-length 120, target py310, lint select `E,F,I,W`.
- `mypy --strict`: new modules must be mypy-clean.
- The tool **must** keep the name `vela_velaql_query` and the `_ro("VelaQL 查询")` annotation (read-only, idempotent) — `tests/test_server.py:23` asserts presence in the readonly set.
- This is a **breaking API change** (input schema changes from `velaql: str` to `view, params, cluster?, response_format`). Document in CHANGELOG; bump version per semver.
- The compiler must **skip `None` values** when building the velaql string (optional params not provided). This is a §11 spec requirement.
- All errors returned to the LLM must be **structured, parseable strings** (not tracebacks).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/mcp_kubevela/velaql/__init__.py` | Create | Re-export `VIEWS`, `VelaQLView`, `compile`, `VelaQLError`, `VelaQLParamError` |
| `src/mcp_kubevela/velaql/views.py` | Create | `VelaQLView` Enum, 6 Pydantic ParamSchemas, `VIEWS` registry mapping view → `ViewSpec` |
| `src/mcp_kubevela/velaql/compiler.py` | Create | Pure function `compile(view, params: dict) -> str`; raises `VelaQLParamError` on bad input |
| `src/mcp_kubevela/velaql/errors.py` | Create | `VelaQLError` base + `VelaQLParamError` (carries `missing: list[str]`, `bad: dict[str, str]`) |
| `src/mcp_kubevela/server.py` | Modify | Replace tool body at lines 801–822; catch `ValidationError` + `VelaQLParamError` → `handle_error` |
| `tests/test_velaql_compiler.py` | Create | Pure-function tests for all 9 views (happy path + None-skipping) |
| `tests/test_velaql_views.py` | Create | ParamSchema tests: required-field errors, type errors, extra-key rejection, defaults |
| `tests/test_server.py` | Modify | Add `test_velaql_query_*` cases (round-trip, missing-param error, schema assertions) |
| `README.md` | Modify | Update the "故障排查" section: 9 view examples; mark old `velaql: str` form as removed |
| `CHANGELOG.md` | Modify | Add entry under unreleased: breaking change to `vela_velaql_query` input schema |

---

### Task 1: `velaql/errors.py` — typed exceptions

**Files:**
- Create: `src/mcp_kubevela/velaql/errors.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class VelaQLError(Exception)` — base, takes `message: str`
  - `class VelaQLParamError(VelaQLError)` — adds `missing: list[str]` (param names absent) and `bad: dict[str, str]` (param name → reason)

- [ ] **Step 1: Write the failing test**

Create `tests/test_velaql_errors.py`:

```python
"""velaql/errors.py — typed exception contract tests"""

from mcp_kubevela.velaql.errors import VelaQLError, VelaQLParamError


def test_velaql_error_is_exception():
    assert issubclass(VelaQLError, Exception)


def test_velaql_error_carries_message():
    e = VelaQLError("something went wrong")
    assert str(e) == "something went wrong"


def test_velaql_param_error_inherits_from_velaql_error():
    assert issubclass(VelaQLParamError, VelaQLError)


def test_velaql_param_error_carries_missing_and_bad():
    e = VelaQLParamError(
        "bad params",
        missing=["appName"],
        bad={"appNs": "must be string, got int"},
    )
    assert e.missing == ["appName"]
    assert e.bad == {"appNs": "must be string, got int"}
    assert "bad params" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_errors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_kubevela.velaql'`

- [ ] **Step 3: Implement `errors.py`**

```python
# src/mcp_kubevela/velaql/errors.py
"""Typed exceptions for the velaql tool.

Distinguishes view-level errors (caller wrote the wrong view) from param-level
errors (caller wrote a wrong/missing param). The tool catches these and turns
them into structured markdown for the LLM.
"""


class VelaQLError(Exception):
    """Base class for all velaql tool errors."""


class VelaQLParamError(VelaQLError):
    """Raised when params fail validation for a known view.

    Attributes:
        missing: param names that were required but absent.
        bad: param name -> human-readable reason for each invalid value.
    """

    def __init__(
        self,
        message: str,
        missing: list[str] | None = None,
        bad: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing: list[str] = list(missing) if missing else []
        self.bad: dict[str, str] = dict(bad) if bad else {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_errors.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add src/mcp_kubevela/velaql/__init__.py src/mcp_kubevela/velaql/errors.py tests/test_velaql_errors.py
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "feat(velaql): add typed exceptions VelaQLError and VelaQLParamError"
```

Note: the `__init__.py` is created empty in Step 3 of this task; subsequent tasks add re-exports to it.

---

### Task 2: `velaql/views.py` — Enum, 6 ParamSchemas, VIEWS registry

**Files:**
- Create: `src/mcp_kubevela/velaql/views.py`
- Create: `src/mcp_kubevela/velaql/__init__.py` (replace empty stub with re-exports)

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class VelaQLView(str, Enum)` with 9 string members matching the view names
  - `class _AppLayerBase(BaseModel)` — `appNs: str` + `appName: str` (required)
  - `class ComponentServiceViewParams(_AppLayerBase)` — adds `name: str | None`, `cluster: str | None`, `clusterNs: str | None` (all `default=None`)
  - `class ServiceViewParams(_AppLayerBase)` — adds `cluster: str | None`, `clusterNs: str | None` (no `name` field)
  - `class PodViewParams(BaseModel)` — `cluster: str`, `namespace: str`, `name: str` (all required)
  - `class ResourceDetailViewParams(BaseModel)` — `cluster: str`, `namespace: str`, `name: str`, `kind: str`, `apiVersion: str` (all required)
  - `class CollectLogsParams(BaseModel)` — `cluster: str`, `namespace: str`, `pod: str`, `container: str` (required); `previous: bool = False`, `timestamps: bool = True`, `tailLines: int = 3000` (defaulted, `ge=1, le=10000`)
  - `class ViewSpec(BaseModel)` — `name: str`, `description: str`, `param_schema: type[BaseModel]`, `example: str`
  - `VIEWS: dict[VelaQLView, ViewSpec]` mapping each of the 9 views to its `ViewSpec`

- [ ] **Step 1: Write the failing test**

Create `tests/test_velaql_views.py`:

```python
"""velaql/views.py — Enum + ParamSchemas + VIEWS registry tests"""

from pydantic import ValidationError
import pytest

from mcp_kubevela.velaql.views import (
    VelaQLView,
    ComponentServiceViewParams,
    ServiceViewParams,
    PodViewParams,
    ResourceDetailViewParams,
    CollectLogsParams,
    VIEWS,
)


def test_velaql_view_enum_has_nine_members():
    assert len(VelaQLView) == 9


def test_views_registry_covers_every_enum_member():
    assert set(VIEWS.keys()) == set(VelaQLView)


@pytest.mark.parametrize("view", list(VelaQLView))
def test_each_view_has_a_param_schema_and_example(view):
    spec = VIEWS[view]
    assert spec.param_schema is not None
    assert spec.example  # non-empty


def test_component_pod_view_requires_appNs_appName():
    with pytest.raises(ValidationError) as exc_info:
        VIEWS[VelaQLView.COMPONENT_POD_VIEW].param_schema.model_validate({})
    err = exc_info.value
    missing = {e["loc"][0] for e in err.errors() if e["type"] == "missing"}
    assert "appNs" in missing
    assert "appName" in missing


def test_component_service_view_allows_optional_omitted():
    # Required base only; no optional fields provided.
    m = ComponentServiceViewParams(appNs="default", appName="demo")
    assert m.name is None
    assert m.cluster is None
    assert m.clusterNs is None


def test_service_view_has_no_name_field():
    fields = set(ServiceViewParams.model_fields.keys())
    assert "name" not in fields
    assert "appNs" in fields
    assert "appName" in fields


def test_pod_view_requires_three_fields():
    with pytest.raises(ValidationError):
        PodViewParams.model_validate({"cluster": "local"})


def test_resource_detail_view_requires_five_fields():
    with pytest.raises(ValidationError):
        ResourceDetailViewParams.model_validate(
            {"cluster": "c", "namespace": "n", "name": "p"}
        )


def test_collect_logs_optional_defaults():
    m = CollectLogsParams(
        cluster="c", namespace="n", pod="p", container="ctr",
    )
    assert m.previous is False
    assert m.timestamps is True
    assert m.tailLines == 3000


def test_collect_logs_tail_lines_bounds():
    base = dict(cluster="c", namespace="n", pod="p", container="ctr")
    with pytest.raises(ValidationError):
        CollectLogsParams(**base, tailLines=0)
    with pytest.raises(ValidationError):
        CollectLogsParams(**base, tailLines=10001)


def test_extra_keys_are_rejected():
    # Spec §11 risk: extra unknown keys must not silently pass.
    with pytest.raises(ValidationError):
        ComponentServiceViewParams.model_validate(
            {"appNs": "x", "appName": "y", "unknownKey": "z"}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_views.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_kubevela.velaql.views'`

- [ ] **Step 3: Implement `views.py`**

```python
# src/mcp_kubevela/velaql/views.py
"""VelaQL view catalog: enum + per-view ParamSchemas + VIEWS registry.

This module is pure data — no I/O, no string formatting. It exists so the LLM
can pick from a known view set and supply typed params, instead of guessing
velaql string templates.

The 9 views covered here are extracted from real production traffic plus
kubevela/velaux source-code audit. See docs/superpowers/specs/2026-07-31-
velaql-tool-stability-design.md §3.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ====================================================================
# 9 VelaQL views
# ====================================================================
class VelaQLView(str, Enum):
    """All VelaQL views the vela_velaql_query tool accepts.

    Adding a new view = add a member here + a ParamSchema below + an entry
    in VIEWS.
    """

    SERVICE_ENDPOINTS_VIEW = "service-endpoints-view"
    APPLICATION_RESOURCE_TREE_VIEW = "application-resource-tree-view"
    SERVICE_APPLIED_RESOURCES_VIEW = "service-applied-resources-view"
    COMPONENT_POD_VIEW = "component-pod-view"
    COMPONENT_SERVICE_VIEW = "component-service-view"
    SERVICE_VIEW = "service-view"
    POD_VIEW = "pod-view"
    APPLICATION_RESOURCE_DETAIL_VIEW = "application-resource-detail-view"
    COLLECT_LOGS = "collect-logs"


# ====================================================================
# ParamSchemas
# ====================================================================
# All schemas use extra='forbid' so unknown keys are rejected
# (matches §11 spec risk: stray keys must not silently pass).


class _AppLayerBase(BaseModel):
    """Required base for all application-level views (1–6)."""

    model_config = ConfigDict(extra="forbid")

    appNs: str = Field(
        ...,
        description=(
            "KubeVela 项目命名空间 (VelaUX 项目列表中的 namespace, "
            "通常与 K8s ns 同名). 与 'namespace' 字段不同."
        ),
    )
    appName: str = Field(
        ...,
        description="应用名 (vela_list_applications 返回的 name 字段)",
    )


class ComponentServiceViewParams(_AppLayerBase):
    """component-service-view{...} — 支持按组件名过滤; 返回结构含 workload / publishVersion / deployVersion."""

    name: str | None = Field(default=None, description="组件名过滤 (可选)")
    cluster: str | None = Field(default=None, description="集群名过滤 (可选)")
    clusterNs: str | None = Field(default=None, description="集群命名空间过滤 (可选)")


class ServiceViewParams(_AppLayerBase):
    """service-view{...} — 不支持 name 过滤; 返回结构含 revision (集群实时状态)."""

    cluster: str | None = Field(default=None, description="集群名过滤 (可选)")
    clusterNs: str | None = Field(default=None, description="集群命名空间过滤 (可选)")


class PodViewParams(BaseModel):
    """pod-view{cluster, namespace, name} — 单个 Pod 详情."""

    model_config = ConfigDict(extra="forbid")

    cluster: str = Field(..., description="目标集群名 (vela_list_clusters 返回的 name)")
    namespace: str = Field(
        ...,
        description="K8s 命名空间 — 注意: 这里是 K8s 真实命名空间, 不是 KubeVela appNs",
    )
    name: str = Field(..., description="Pod 名 (含 deployment 哈希后缀)")


class ResourceDetailViewParams(BaseModel):
    """application-resource-detail-view{cluster, namespace, name, kind, apiVersion}.

    kind / apiVersion 必须配对 (Pod↔v1, Deployment↔apps/v1 等).
    配对错误由 VelaUX 端拒绝 — 客户端不强制.
    """

    model_config = ConfigDict(extra="forbid")

    cluster: str = Field(..., description="目标集群名")
    namespace: str = Field(..., description="K8s 命名空间")
    name: str = Field(..., description="资源名 (Pod/Deployment/etc. 的 metadata.name)")
    kind: str = Field(
        ...,
        description="K8s 资源 Kind, 如 Pod / Deployment / ReplicaSet / Application / HelmRelease",
    )
    apiVersion: str = Field(
        ...,
        description="K8s apiVersion, 如 v1 / apps/v1 / core.oam.dev/v1beta1 / helm.toolkit.fluxcd.io/v2beta1",
    )


class CollectLogsParams(BaseModel):
    """collect-logs{...} — Pod 日志. 4 必填 + 3 可选."""

    model_config = ConfigDict(extra="forbid")

    cluster: str = Field(..., description="目标集群名")
    namespace: str = Field(..., description="K8s 命名空间")
    pod: str = Field(..., description="Pod 名")
    container: str = Field(..., description="容器名 (Pod 内多容器时必填)")
    previous: bool = Field(default=False, description="true=取重启前日志; false=取当前日志")
    timestamps: bool = Field(default=True, description="是否在每行加时间戳")
    tailLines: int = Field(default=3000, description="尾部行数", ge=1, le=10000)


# ====================================================================
# ViewSpec + VIEWS registry
# ====================================================================
class ViewSpec(BaseModel):
    """Registry entry: the canonical name, what the LLM should understand
    it does, the schema to validate params against, and a worked example.
    """

    name: str
    description: str
    param_schema: type[BaseModel]
    example: str

    # ConfigDict arbitrary_types_allowed is needed for type[BaseModel] above
    model_config = ConfigDict(arbitrary_types_allowed=True)


VIEWS: dict[VelaQLView, ViewSpec] = {
    VelaQLView.SERVICE_ENDPOINTS_VIEW: ViewSpec(
        name="service-endpoints-view",
        description="查询应用 Service 的 Endpoints (后端 Pod IP 列表).",
        param_schema=_AppLayerBase,
        example="service-endpoints-view{appNs=default,appName=demo}.status",
    ),
    VelaQLView.APPLICATION_RESOURCE_TREE_VIEW: ViewSpec(
        name="application-resource-tree-view",
        description="查询应用所有 K8s 资源的层级树.",
        param_schema=_AppLayerBase,
        example="application-resource-tree-view{appNs=default,appName=demo}.status",
    ),
    VelaQLView.SERVICE_APPLIED_RESOURCES_VIEW: ViewSpec(
        name="service-applied-resources-view",
        description="查询 Service 关联的 K8s 资源 (selector 匹配到的 Deployment 等).",
        param_schema=_AppLayerBase,
        example="service-applied-resources-view{appNs=default,appName=demo}.status",
    ),
    VelaQLView.COMPONENT_POD_VIEW: ViewSpec(
        name="component-pod-view",
        description="查询应用下所有组件的 Pod 列表.",
        param_schema=_AppLayerBase,
        example="component-pod-view{appNs=default,appName=demo}.status",
    ),
    VelaQLView.COMPONENT_SERVICE_VIEW: ViewSpec(
        name="component-service-view",
        description="查询应用下所有 Service (含 workload / publishVersion / deployVersion); 支持按组件名过滤.",
        param_schema=ComponentServiceViewParams,
        example="component-service-view{appNs=default,appName=demo,name=webservice}.status",
    ),
    VelaQLView.SERVICE_VIEW: ViewSpec(
        name="service-view",
        description="查询应用 Service 集群实时状态 (含 revision); 不支持按组件名过滤.",
        param_schema=ServiceViewParams,
        example="service-view{appNs=default,appName=demo}.status",
    ),
    VelaQLView.POD_VIEW: ViewSpec(
        name="pod-view",
        description="查询单个 Pod 详情 (运行时数据, 实时状态).",
        param_schema=PodViewParams,
        example="pod-view{cluster=local,namespace=default,name=demo-5ffb6c4b-wxb54}.status",
    ),
    VelaQLView.APPLICATION_RESOURCE_DETAIL_VIEW: ViewSpec(
        name="application-resource-detail-view",
        description="查询某个 K8s 资源的详情; kind/apiVersion 配对错误由 VelaUX 端拒绝.",
        param_schema=ResourceDetailViewParams,
        example="application-resource-detail-view{cluster=local,namespace=default,name=demo,kind=Pod,apiVersion=v1}.status",
    ),
    VelaQLView.COLLECT_LOGS: ViewSpec(
        name="collect-logs",
        description="查询 Pod 容器日志 (支持 previous/timestamps/tailLines 可选参数).",
        param_schema=CollectLogsParams,
        example="collect-logs{cluster=local,namespace=default,pod=demo-xxx,container=demo,tailLines=3000}.logs",
    ),
}
```

- [ ] **Step 4: Replace `__init__.py` with re-exports**

```python
# src/mcp_kubevela/velaql/__init__.py
"""velaql: schema-typed VelaQL view catalog and compiler.

Public API:
    VelaQLView  — enum of all 9 supported views
    VIEWS       — view -> ViewSpec registry
    compile     — pure function: (view, params) -> velaql string
    VelaQLError, VelaQLParamError — typed exceptions
"""

from mcp_kubevela.velaql.errors import VelaQLError, VelaQLParamError
from mcp_kubevela.velaql.views import VelaQLView, VIEWS

__all__ = [
    "VelaQLView",
    "VIEWS",
    "VelaQLError",
    "VelaQLParamError",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_views.py -v`
Expected: PASS (all 9 tests in the file)

- [ ] **Step 6: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add src/mcp_kubevela/velaql/__init__.py src/mcp_kubevela/velaql/views.py tests/test_velaql_views.py
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "feat(velaql): add VelaQLView enum + 6 ParamSchemas + VIEWS registry"
```

---

### Task 3: `velaql/compiler.py` — pure (view, params) -> velaql string

**Files:**
- Create: `src/mcp_kubevela/velaql/compiler.py`
- Modify: `src/mcp_kubevela/velaql/__init__.py` (add re-export)

**Interfaces:**
- Consumes: `VIEWS` (from views.py), `VelaQLParamError` (from errors.py)
- Produces: `compile(view: VelaQLView, params: dict[str, Any]) -> str` — raises `VelaQLParamError` on validation failure, otherwise returns the canonical velaql string (e.g. `"component-pod-view{appNs=default,appName=demo}.status"`).
- Behavior contract:
  - Calls `VIEWS[view].param_schema.model_validate(params)`
  - On `ValidationError`, translates to `VelaQLParamError` carrying `missing` and `bad`
  - Skips `None` values when building the key=value pairs (per §11 spec)
  - Strips spaces around `=` and `,` in the output (matches VelaUX UI URL samples: no spaces between keys)
  - Appends `.status` (or `.logs` for `COLLECT_LOGS`) — hardcoded in compiler, not in user input

- [ ] **Step 1: Write the failing test**

Create `tests/test_velaql_compiler.py`:

```python
"""velaql/compiler.py — pure (view, params) -> velaql string tests"""

import pytest
from pydantic import ValidationError

from mcp_kubevela.velaql.compiler import compile
from mcp_kubevela.velaql.errors import VelaQLParamError
from mcp_kubevela.velaql.views import VelaQLView


def test_compile_component_pod_view_minimal():
    out = compile(
        VelaQLView.COMPONENT_POD_VIEW,
        {"appNs": "devops-admin-test", "appName": "xdevops-ui"},
    )
    assert out == "component-pod-view{appNs=devops-admin-test,appName=xdevops-ui}.status"


def test_compile_collect_logs_with_defaults_omitted():
    out = compile(
        VelaQLView.COLLECT_LOGS,
        {
            "cluster": "devops-test",
            "namespace": "devops-admin",
            "pod": "xdevops-ui-5ffb6c4b-wxb54",
            "container": "xdevops-ui",
        },
    )
    # Optional fields (previous/timestamps/tailLines) must NOT appear when not provided.
    assert out == (
        "collect-logs{cluster=devops-test,namespace=devops-admin,"
        "pod=xdevops-ui-5ffb6c4b-wxb54,container=xdevops-ui}.logs"
    )


def test_compile_collect_logs_with_explicit_optional():
    out = compile(
        VelaQLView.COLLECT_LOGS,
        {
            "cluster": "c",
            "namespace": "n",
            "pod": "p",
            "container": "ctr",
            "previous": True,
            "timestamps": False,
            "tailLines": 500,
        },
    )
    # Order matches Pydantic field declaration order in CollectLogsParams.
    assert out == (
        "collect-logs{cluster=c,namespace=n,pod=p,container=ctr,"
        "previous=true,timestamps=false,tailLines=500}.logs"
    )


def test_compile_component_service_view_optionals_omitted():
    out = compile(
        VelaQLView.COMPONENT_SERVICE_VIEW,
        {"appNs": "x", "appName": "y"},
    )
    assert out == "component-service-view{appNs=x,appName=y}.status"


def test_compile_component_service_view_with_name():
    out = compile(
        VelaQLView.COMPONENT_SERVICE_VIEW,
        {"appNs": "x", "appName": "y", "name": "webservice"},
    )
    assert out == "component-service-view{appNs=x,appName=y,name=webservice}.status"


def test_compile_resource_detail_view_appends_status():
    out = compile(
        VelaQLView.APPLICATION_RESOURCE_DETAIL_VIEW,
        {
            "cluster": "devops-test",
            "namespace": "devops-admin",
            "name": "xdevops-ui-5ffb6c4b-wxb54",
            "kind": "Pod",
            "apiVersion": "v1",
        },
    )
    assert out == (
        "application-resource-detail-view{cluster=devops-test,namespace=devops-admin,"
        "name=xdevops-ui-5ffb6c4b-wxb54,kind=Pod,apiVersion=v1}.status"
    )


def test_compile_pod_view():
    out = compile(
        VelaQLView.POD_VIEW,
        {"cluster": "c", "namespace": "n", "name": "p"},
    )
    assert out == "pod-view{cluster=c,namespace=n,name=p}.status"


def test_compile_raises_on_missing_required():
    with pytest.raises(VelaQLParamError) as exc_info:
        compile(VelaQLView.COMPONENT_POD_VIEW, {"appNs": "x"})
    assert "appName" in exc_info.value.missing
    assert exc_info.value.bad == {}


def test_compile_raises_on_extra_key():
    with pytest.raises(VelaQLParamError) as exc_info:
        compile(
            VelaQLView.COMPONENT_POD_VIEW,
            {"appNs": "x", "appName": "y", "junk": "z"},
        )
    # 'junk' is reported in bad, not missing.
    assert "junk" in exc_info.value.bad


def test_compile_raises_on_wrong_type():
    with pytest.raises(VelaQLParamError) as exc_info:
        compile(
            VelaQLView.COLLECT_LOGS,
            {"cluster": "c", "namespace": "n", "pod": "p", "container": "ctr", "tailLines": "not an int"},
        )
    assert "tailLines" in exc_info.value.bad
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_compiler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_kubevela.velaql.compiler'`

- [ ] **Step 3: Implement `compiler.py`**

```python
# src/mcp_kubevela/velaql/compiler.py
"""Pure compiler: (VelaQLView, params dict) -> velaql string.

The compiler is the only place that knows how to translate typed params
into the wire-format string. It:

  * Validates the params against the view's ParamSchema (Pydantic v2).
  * Translates pydantic.ValidationError into VelaQLParamError with
    structured `missing` and `bad` fields so the tool can render a
    human-readable (and LLM-parseable) error message.
  * Skips None values — optional params that the caller did not provide
    are absent from the output (per spec §11).
  * Emits key=value pairs in Pydantic field declaration order
    (model_fields iteration).
  * Appends the canonical suffix (.status / .logs).
"""

from typing import Any

from pydantic import ValidationError

from mcp_kubevela.velaql.errors import VelaQLParamError
from mcp_kubevela.velaql.views import VIEWS, VelaQLView


# Canonical suffix per view. Hardcoded because VelaUX only accepts these
# two for the views we support.
_SUFFIX: dict[VelaQLView, str] = {
    VelaQLView.COLLECT_LOGS: ".logs",
}


def _format_value(v: Any) -> str:
    """Render a Pydantic-validated value into its wire string form."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def compile(view: VelaQLView, params: dict[str, Any]) -> str:
    """Validate `params` against `view`'s ParamSchema, then format velaql.

    Raises:
        VelaQLParamError: with `missing` and `bad` populated from
            the underlying Pydantic ValidationError.
    """
    spec = VIEWS[view]
    try:
        validated = spec.param_schema.model_validate(params)
    except ValidationError as exc:
        missing: list[str] = []
        bad: dict[str, str] = {}
        for err in exc.errors():
            loc = err.get("loc", ())
            name = loc[0] if loc else "<root>"
            if err.get("type") == "missing":
                if name not in missing:
                    missing.append(name)
            else:
                # First sentence of the message — enough for the LLM to act.
                msg = err.get("msg", "invalid value")
                bad[name] = f"{msg} (got {err.get('input')!r})"
        raise VelaQLParamError(
            f"params for view '{view.value}' failed validation",
            missing=missing,
            bad=bad,
        ) from exc

    # Build the {k=v,k=v} body in Pydantic field declaration order.
    parts: list[str] = []
    for field_name in type(validated).model_fields:
        value = getattr(validated, field_name)
        if value is None:
            # Optional field not provided — skip per spec §11.
            continue
        parts.append(f"{field_name}={_format_value(value)}")

    suffix = _SUFFIX.get(view, ".status")
    return f"{view.value}{{{','.join(parts)}}}{suffix}"
```

- [ ] **Step 4: Update `__init__.py` to re-export `compile`**

```python
# src/mcp_kubevela/velaql/__init__.py
"""velaql: schema-typed VelaQL view catalog and compiler.

Public API:
    VelaQLView  — enum of all 9 supported views
    VIEWS       — view -> ViewSpec registry
    compile     — pure function: (view, params) -> velaql string
    VelaQLError, VelaQLParamError — typed exceptions
"""

from mcp_kubevela.velaql.compiler import compile
from mcp_kubevela.velaql.errors import VelaQLError, VelaQLParamError
from mcp_kubevela.velaql.views import VelaQLView, VIEWS

__all__ = [
    "VelaQLView",
    "VIEWS",
    "compile",
    "VelaQLError",
    "VelaQLParamError",
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_compiler.py -v`
Expected: PASS (all 10 tests)

- [ ] **Step 6: Run the full test suite — make sure nothing else broke**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_velaql_*.py tests/test_velaql_errors.py -v`
Expected: PASS (errors, views, compiler — 4 + 9 + 10 = 23 tests)

- [ ] **Step 7: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add src/mcp_kubevela/velaql/__init__.py src/mcp_kubevela/velaql/compiler.py tests/test_velaql_compiler.py
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "feat(velaql): add pure compiler (view, params) -> velaql string"
```

---

### Task 4: Wire `vela_velaql_query` tool in `server.py` to the new compiler

**Files:**
- Modify: `src/mcp_kubevela/server.py:801-822` (replace the `vela_velaql_query` tool body)

**Interfaces:**
- Consumes: `compile`, `VelaQLView`, `VIEWS`, `VelaQLParamError`, `handle_error`, `ResponseFormat`, `get_vela_client`, `to_json` (all from existing code or the new velaql package)
- Produces: a new `vela_velaql_query` tool with signature:

  ```python
  @mcp.tool(name="vela_velaql_query", annotations=_ro("VelaQL 查询"))
  async def vela_velaql_query(
      view: Annotated[VelaQLView, Field(description="视图。必须是受支持 view 之一。")],
      params: Annotated[dict[str, Any], Field(
          description=(
              "视图参数 (JSON 对象)。键名见 view 描述: "
              "service-endpoints-view / application-resource-tree-view / service-applied-resources-view / component-pod-view -> {appNs, appName}; "
              "component-service-view -> {appNs, appName, [name, cluster, clusterNs]}; "
              "service-view -> {appNs, appName, [cluster, clusterNs]}; "
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
  ) -> str
  ```

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server.py` (end of file, after existing tests):

```python
"""vela_velaql_query tool — end-to-end tool tests"""

import pytest

from mcp_kubevela import server


async def test_velaql_query_tool_registered():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert "vela_velaql_query" in tools


def test_velaql_query_input_schema_requires_view_and_params():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "vela_velaql_query")
    schema = tool.input_schema
    assert set(schema["required"]) == {"view", "params"}
    # view is an enum
    view_schema = schema["properties"]["view"]
    assert "enum" in view_schema
    assert len(view_schema["enum"]) == 9
    assert "component-pod-view" in view_schema["enum"]
    assert "collect-logs" in view_schema["enum"]
    assert "application-resource-detail-view" in view_schema["enum"]
    # params is a free-form object
    assert schema["properties"]["params"]["type"] == "object"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_server.py::test_velaql_query_input_schema_requires_view_and_params -v`
Expected: FAIL (the current schema only has `velaql: str` required; `view` and `params` are not yet properties)

- [ ] **Step 3: Replace the tool definition in `server.py`**

Edit `src/mcp_kubevela/server.py`. Find the existing block (around line 801-822):

```python
@mcp.tool(name="vela_velaql_query", annotations=_ro("VelaQL 查询"))
async def vela_velaql_query(
    velaql: Annotated[str, Field(
        description=(
            "VelaQL 查询语句，如 "
            'component-pod-view{appNs=default,appName=demo}.status 或 '
            'collect-logs{cluster=local,namespace=default,pod=xxx}.logs'
        ),
        min_length=1,
    )],
) -> str:
    """执行 VelaQL 查询（Pod 列表、容器日志、资源拓扑等运行时数据）。

    对应 API：GET /api/v1/query?velaql=
    常用视图：component-pod-view、component-service-view、collect-logs、service-view
    """
    try:
        client = await get_vela_client()
        data = await client.velaql_query(velaql)
        return f"# VelaQL 查询结果\n\n```json\n{to_json(data)}\n```"
    except Exception as e:
        return handle_error(e)
```

Replace it with:

```python
@mcp.tool(name="vela_velaql_query", annotations=_ro("VelaQL 查询"))
async def vela_velaql_query(
    view: Annotated[VelaQLView, Field(description="视图。必须是受支持 view 之一。")],
    params: Annotated[dict[str, Any], Field(
        description=(
            "视图参数 (JSON 对象)。键名见 view 描述: "
            "service-endpoints-view / application-resource-tree-view / service-applied-resources-view / component-pod-view -> {appNs, appName}; "
            "component-service-view -> {appNs, appName, [name, cluster, clusterNs]}; "
            "service-view -> {appNs, appName, [cluster, clusterNs]}; "
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
    """执行 VelaQL 查询（Pod 列表、容器日志、资源拓扑等运行时数据）。

    对应 API：GET /api/v1/query?velaql=
    使用 view 枚举 + 结构化 params (见各 view 的 ParamSchema 描述),
    由服务器拼装 velaql 字符串。错误以结构化文本返回, LLM 可直接 parse 修复后重试。
    """
    # 1) 编译 (Pydantic 校验 + 拼装)
    try:
        velaql_str = compile(view, params)
    except VelaQLParamError as e:
        # 已知参数错误: 返回结构化文本, 不抛栈
        spec = VIEWS[view]
        lines = [f"VelaQL 参数错误: {e}", ""]
        if e.missing:
            lines.append("**缺失必填参数**:")
            for name in e.missing:
                lines.append(f"- `{name}`")
            lines.append("")
        if e.bad:
            lines.append("**非法参数值**:")
            for name, reason in e.bad.items():
                lines.append(f"- `{name}`: {reason}")
            lines.append("")
        lines.append(f"参考示例: `{spec.example}`")
        return "\n".join(lines)

    # 2) 调 VelaUX
    try:
        client = await get_vela_client()
        data = await client.velaql_query(velaql_str)
    except Exception as e:
        return handle_error(e)

    # 3) 渲染
    if response_format == ResponseFormat.JSON:
        return to_json(data)
    return f"# VelaQL 查询结果\n\n```json\n{to_json(data)}\n```"
```

- [ ] **Step 4: Add the new imports to `server.py`**

At the top of `src/mcp_kubevela/server.py`, find the existing `from typing import Annotated, Any, Optional` (around line 22) — it already has `Any`, so nothing to add there. Below the existing client/velaql imports section, add:

```python
from mcp_kubevela.velaql import (
    VelaQLView,
    VIEWS,
    VelaQLParamError,
    compile,
)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_server.py::test_velaql_query_tool_registered tests/test_server.py::test_velaql_query_input_schema_requires_view_and_params -v`
Expected: PASS

- [ ] **Step 6: Run the full test suite to ensure nothing else broke**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/ -v`
Expected: PASS for everything. (The existing `EXPECTED_READONLY` set at `tests/test_server.py:9-31` includes `vela_velaql_query` — that test still passes because we kept the name.)

- [ ] **Step 7: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add src/mcp_kubevela/server.py tests/test_server.py
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "feat(velaql): wire vela_velaql_query tool to schema-typed (view, params) interface"
```

---

### Task 5: Add tool-level end-to-end tests with respx mock

**Files:**
- Modify: `tests/test_server.py` (append end-to-end tests)

**Interfaces:**
- Consumes: existing respx mock pattern (look for `respx_mock` usage in the repo before writing — if not present, use `monkeypatch` to stub the `get_vela_client` / `client.velaql_query` path)
- Produces: 3 new tests that exercise the full tool body: happy path, missing-param path, downstream error path

- [ ] **Step 1: Verify existing respx usage in the repo**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && grep -r "respx" tests/ src/`
Expected: shows existing `respx_mock` fixtures in test files. If absent, fall back to the monkeypatch approach below.

- [ ] **Step 2: Write the end-to-end tests**

Append to `tests/test_server.py`:

```python
"""vela_velaql_query end-to-end — exercise full tool body via in-memory mocks."""

import pytest

from mcp_kubevela import server


class _FakeClient:
    """Stand-in for VelaUX client used by the tool's happy path."""

    def __init__(self, response: dict) -> None:
        self._response = response
        self.calls: list[str] = []

    async def velaql_query(self, velaql: str) -> dict:
        self.calls.append(velaql)
        return self._response


@pytest.fixture
def fake_client(monkeypatch):
    """Patches get_vela_client to return a _FakeClient controllable per test."""
    holder: dict = {}

    async def _stub_get():
        return holder["client"]

    monkeypatch.setattr(server, "get_vela_client", _stub_get)

    def _make(response: dict) -> _FakeClient:
        holder["client"] = _FakeClient(response)
        return holder["client"]

    return _make


async def test_velaql_query_happy_path_component_pod(fake_client):
    client = fake_client({"status": {"pods": [{"name": "demo-abc-xyz"}]}})
    result = await server.vela_velaql_query.fn(
        view="component-pod-view",
        params={"appNs": "devops-admin-test", "appName": "xdevops-ui"},
    )
    # Compiler produced the right wire string and we passed it through.
    assert client.calls == [
        "component-pod-view{appNs=devops-admin-test,appName=xdevops-ui}.status"
    ]
    # Output mentions the view name and renders the data.
    assert "VelaQL 查询结果" in result
    assert "demo-abc-xyz" in result


async def test_velaql_query_missing_required_param(fake_client):
    client = fake_client({})  # would be called if validation passed — must NOT happen
    result = await server.vela_velaql_query.fn(
        view="component-pod-view",
        params={"appNs": "x"},  # missing appName
    )
    # Server-side validation rejected the call; we never hit VelaUX.
    assert client.calls == []
    # Output names the missing field so the LLM can self-correct.
    assert "缺失必填参数" in result
    assert "appName" in result


async def test_velaql_query_unknown_view_returns_enum_error(fake_client):
    client = fake_client({})
    # FastMCP validates the enum before the body runs, so we expect
    # a ValidationError to propagate. We just assert it doesn't reach
    # the client.
    with pytest.raises(Exception):
        await server.vela_velaql_query.fn(
            view="totally-bogus-view",
            params={"appNs": "x", "appName": "y"},
        )
    assert client.calls == []


async def test_velaql_query_collect_logs_with_optional_defaults(fake_client):
    client = fake_client({"status": {"logs": "line1\nline2"}})
    await server.vela_velaql_query.fn(
        view="collect-logs",
        params={
            "cluster": "devops-test",
            "namespace": "devops-admin",
            "pod": "xdevops-ui-5ffb6c4b-wxb54",
            "container": "xdevops-ui",
        },
    )
    # Optional params (previous/timestamps/tailLines) MUST be absent from wire string.
    wire = client.calls[0]
    assert "previous" not in wire
    assert "timestamps" not in wire
    assert "tailLines" not in wire
    assert wire.endswith(".logs")
```

- [ ] **Step 3: Run the new tests**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/test_server.py -v -k "velaql_query_"`
Expected: PASS for all 4 new tests

If `server.vela_velaql_query.fn` does not exist (depends on FastMCP version), use the public function name directly. Inspect with:

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && python -c "from mcp_kubevela import server; print(dir(server.vela_velaql_query))"
```

…and pick the right attribute (could be `fn`, `func`, or just call `server.vela_velaql_query(...)` directly as an async function).

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/ -v`
Expected: PASS for everything

- [ ] **Step 5: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add tests/test_server.py
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "test(velaql): add end-to-end tool tests with fake client"
```

---

### Task 6: Lint + type-check the new code

**Files:**
- No new files; only verify the new modules pass project quality gates.

- [ ] **Step 1: Run ruff**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/ruff check src/mcp_kubevela/velaql/ src/mcp_kubevela/server.py tests/test_velaql_*.py tests/test_velaql_errors.py`
Expected: no errors. If ruff complains, fix the issues inline (e.g., import ordering under `I`).

- [ ] **Step 2: Run mypy strict**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/mypy src/mcp_kubevela/velaql/`
Expected: `Success: no issues found`. If errors:
- `type[BaseModel]` in `ViewSpec` needs `ConfigDict(arbitrary_types_allowed=True)` (already added in Task 2)
- Any other mypy error gets fixed inline in the relevant file

- [ ] **Step 3: Commit any fixes (if needed)**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add -u
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "style(velaql): fix ruff + mypy issues" --allow-empty
```

(Use `--allow-empty` so the commit is skipped if there were no fixes.)

---

### Task 7: Update README + CHANGELOG

**Files:**
- Modify: `README.md` — find the "故障排查" section's mention of `vela_velaql_query` and update to reflect 9 views
- Modify: `CHANGELOG.md` — add an unreleased entry for the breaking change

- [ ] **Step 1: Find the current usage examples in README**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && grep -n "vela_velaql_query\|VelaQL" README.md`
Expected: at least one match in the "故障排查" section.

- [ ] **Step 2: Update the README example block**

Find the line containing:

```text
用 VelaQL 查一下 demo 在 prod 环境的 Pod 列表，有没有在重启的
```

Update the parenthetical to:

```text
（`vela_velaql_query`，`view=component-pod-view`，`params={appNs, appName}`）
```

In the "可用工具" table, find the row for `vela_velaql_query` and update the "说明" column to mention "9 个支持的 view" and reference the spec.

- [ ] **Step 3: Update CHANGELOG**

Read `CHANGELOG.md` to find the "Unreleased" or top section. Prepend an entry like:

```markdown
## [Unreleased]

### Breaking Changes

- **`vela_velaql_query` 输入参数重构**: 旧的自由字符串 `velaql: str` 改为 schema-typed `(view, params)`
  - `view` 现在是 9 选 1 的枚举 (component-pod-view / collect-logs / service-view / 等)
  - `params` 是结构化 JSON 字典, 由对应 view 的 Pydantic schema 校验
  - 已知 view 的参数错误以可解析的 markdown 文本返回, LLM 可直接重试
  - 详见 docs/superpowers/specs/2026-07-31-velaql-tool-stability-design.md
```

- [ ] **Step 4: Verify README rendering**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && grep -n "vela_velaql_query" README.md`
Expected: the updated references are visible.

- [ ] **Step 5: Commit**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git add README.md CHANGELOG.md
git -c user.name="Sisyphus" -c user.email="Sisyphus@local" commit -m "docs: document vela_velaql_query breaking change in README + CHANGELOG"
```

---

### Task 8: Final end-to-end verification

**Files:**
- No changes; just run the full test suite and verify production-shaped behavior.

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && .venv/bin/pytest tests/ -v`
Expected: PASS for every test, including the original 7 from `tests/test_server.py:9-101`.

- [ ] **Step 2: Smoke-test the tool against a real VelaUX instance (optional)**

If a VelaUX instance is available, run a one-shot call:

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && \
  VELA_URL=http://localhost:8000 \
  VELA_USERNAME=admin \
  VELA_PASSWORD=xxx \
  .venv/bin/python -c "
import asyncio
from mcp_kubevela import server
result = asyncio.run(
    server.vela_velaql_query.fn(
        view='component-pod-view',
        params={'appNs': 'default', 'appName': 'demo'},
    )
)
print(result[:200])
"
```

Expected: a markdown block with `VelaQL 查询结果` heading and the JSON payload from VelaUX.

- [ ] **Step 3: Confirm the working tree is clean**

Run: `cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git status --short`
Expected: only `.omo/` (or similar) is untracked; no modified tracked files.

- [ ] **Step 4: Summarize the change**

```bash
cd /Users/zhouwei/Documents/AI/MCP/mcp-kubevela && git log --oneline 643cf26..HEAD
```

Expected: 7 commits ahead of `643cf26` (the original spec commit), one per task above. Each commit message should match the format `feat(velaql): …` / `test(velaql): …` / `docs: …` / `style(velaql): …`.

---

## Self-Review

**1. Spec coverage** — checking each spec section against this plan:

| Spec section | Covered by |
|---|---|
| §1 Problem statement (5 issues) | Tasks 1–5 address all 5: enum (Task 2), param semantics via description (Task 4), required/optional via Pydantic defaults (Task 2), structured errors (Tasks 1, 3, 4), structured tool desc (Task 4) |
| §2 Goals (table) | All 5 rows covered by the same tasks |
| §3 View catalog (9 views) | All 9 in Task 2's `VIEWS` registry |
| §4 Architecture (file layout) | Tasks 1–3 implement exactly that layout |
| §5 Data flow (happy + error) | Task 4 implements happy path + error path; Task 5 tests it |
| §6 Pydantic schema | Task 2 implements all 6 schemas; Task 3 honors the `extra='forbid'` requirement; Task 4 includes the full tool signature with the rich `params` description |
| §7 D1 (no raw mode) | Honored — only the structured path is implemented; no `mode=` parameter |
| §7 D2 (cluster on top) | Honored — Task 4 includes top-level `cluster` param with placeholder description |
| §7 D3 (no Literal for free strings) | Honored — only `str = Field(...)` for appNs/appName/etc. |
| §7 D4 (no autocomplete) | Honored — no enum for appNs/appName |
| §8 Test strategy (8.1, 8.2, 8.3) | 8.1 → Task 3; 8.2 → Task 5; 8.3 → existing `test_all_tools_registered` keeps passing (tool name unchanged) |
| §9 Migration (breaking change) | Task 7 CHANGELOG entry |
| §10 Implementation order (6 steps) | Tasks 1–5 + 7 map 1:1; Task 6 is the lint gate; Task 8 is the smoke test |
| §11 Risks (5 rows) | Each mitigation applied: enum prevents wrong view (Task 2), descriptions disambiguate (Task 2), structured error catch (Task 4), CHANGELOG (Task 7), compiler skips None (Task 3) |

**2. Placeholder scan** — no "TBD" / "TODO" / "implement later" / "similar to Task N" in the plan. All code blocks contain real code. All function signatures are spelled out, with parameter and return types.

**3. Type consistency** — checked: `compile(view: VelaQLView, params: dict[str, Any]) -> str` is used identically in Task 3, Task 4, and Task 5. `VelaQLParamError.missing: list[str]` and `VelaQLParamError.bad: dict[str, str]` are defined in Task 1 and consumed in Tasks 3, 4, 5. `ViewSpec.param_schema: type[BaseModel]` is defined in Task 2 and consumed in Task 3.

No gaps detected. Plan is self-contained and executable.
