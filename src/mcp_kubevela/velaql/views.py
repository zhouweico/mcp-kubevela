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
