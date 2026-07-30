"""KubeVela MCP Server

提供与 KubeVela (VelaUX) 应用交付平台交互的 MCP 工具。
通过 VelaUX REST API（/api/v1，JWT Bearer 认证）操作应用、工作流、环境、插件等。

环境变量：
    VELA_URL        VelaUX 地址（默认 http://localhost:8000）
    VELA_USERNAME   登录用户名（必填）
    VELA_PASSWORD   登录密码（必填）
    VELA_READ_ONLY  true 时只注册只读工具
    MCP_TRANSPORT   stdio（默认）/ sse / streamable-http
"""

from __future__ import annotations

import logging
import os
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Annotated, Any, Optional

import httpx2 as httpx
from mcp.server import MCPServer
from mcp.server.elicitation import AcceptedElicitation
from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import TokenAuthMiddleware
from .clients import VelaApiError, VelaAuthError, get_vela_client, reset_vela_client
from .render import fmt_status, render_kv, render_list, to_json

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    """管理客户端生命周期：启动时预创建客户端，关闭时清理 HTTP 连接。"""
    client = await get_vela_client()
    try:
        yield {"client": client}
    finally:
        try:
            await client.aclose()
        except Exception:
            logger.warning("客户端关闭异常", exc_info=True)
        finally:
            reset_vela_client()


mcp = MCPServer(
    "kubevela_mcp",
    instructions=(
        "KubeVela (VelaUX) 应用交付平台 MCP Server。"
        "通过 VelaUX REST API 操作应用、工作流、环境、插件、集群等资源。"
        "写操作（创建/部署/回滚/终止）在 VELA_READ_ONLY=true 时不可用。"
    ),
    lifespan=app_lifespan,
)


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class _ConfirmSchema(BaseModel):
    """MRTR 确认表单 schema"""
    confirm: bool = Field(..., description="确认执行此操作")


async def _confirm_action(ctx: Optional[Context], action_desc: str) -> tuple[bool, str]:
    """通过 MRTR 确认破坏性操作。

    ctx 不可用时降级为直接执行。
    """
    if not ctx:
        return True, ""
    try:
        result = await ctx.elicit(
            f"⚠️ 确认{action_desc}？此操作不可逆。",
            _ConfirmSchema,
        )
        if isinstance(result, AcceptedElicitation) and result.data.confirm:
            return True, ""
        return False, "已取消操作"
    except Exception:
        logger.exception("确认流程异常：%s", action_desc)
        return False, "确认流程异常，已中止操作"


# ==================== 错误处理 ====================
def handle_error(e: Exception) -> str:
    """统一错误处理：转换为中文可读提示，不抛栈。"""
    if isinstance(e, VelaAuthError):
        logger.warning("认证异常: %s", e)
    elif isinstance(e, VelaApiError) and e.status_code in (401, 403, 404):
        logger.warning("业务异常: HTTP %d", e.status_code)
    elif isinstance(e, (httpx.TimeoutException, httpx.ConnectError)):
        logger.warning("网络异常: %s", type(e).__name__)
    else:
        logger.error("工具调用异常", exc_info=e)

    if isinstance(e, VelaAuthError):
        return f"错误：{e}"
    if isinstance(e, VelaApiError):
        if e.status_code == 401:
            return "错误：认证失败，请检查 VELA_USERNAME / VELA_PASSWORD 是否正确"
        if e.status_code == 403:
            return (
                f"错误：权限不足（RBAC 拒绝），请确认该用户拥有对应项目/资源的权限。"
                f"详情：{e.message}"
            )
        if e.status_code == 404:
            return f"错误：资源不存在，请检查应用名/环境名/资源名是否正确。详情：{e.message}"
        return (
            f"错误：VelaUX 请求失败（HTTP {e.status_code}，"
            f"业务码 {e.business_code}）：{e.message}"
        )
    if isinstance(e, httpx.TimeoutException):
        return "错误：请求超时，请检查 VelaUX 服务是否可用"
    if isinstance(e, httpx.ConnectError):
        return "错误：无法连接到 VelaUX，请检查 VELA_URL"
    if isinstance(e, ValueError):
        return f"错误：{e}"
    return f"错误：{type(e).__name__}: {e}"


def _ro(title: str) -> ToolAnnotations:
    """只读工具注解"""
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=True,
    )


def _rw(title: str, destructive: bool = False, idempotent: bool = False) -> ToolAnnotations:
    """写工具注解"""
    return ToolAnnotations(
        title=title,
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )



class DefinitionType(str, Enum):
    COMPONENT = "component"
    TRAIT = "trait"
    POLICY = "policy"
    WORKFLOWSTEP = "workflowstep"


# ==================== 结构化输出模型 ====================
class _StructuredOutput(BaseModel):
    """结构化输出基类。

    允许 structured_content=None（错误路径不产生结构化数据）：
    通过 before-validator 将 None 转为空 dict，使 Pydantic 校验通过，
    而实际返回的 CallToolResult.structured_content 仍为 None。

    extra='allow' 保留 API 返回的额外字段，避免 structured_content 与 schema 不匹配。
    """

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _accept_none(cls, data: Any) -> Any:
        return data if data is not None else {}


class ListApplicationsOutput(_StructuredOutput):
    """vela_list_applications 的结构化输出"""

    applications: Optional[list[dict[str, Any]]] = None


class GetApplicationOutput(_StructuredOutput):
    """vela_get_application 的结构化输出（应用详情，字段透传 API 响应）"""

    name: Optional[str] = None
    alias: Optional[str] = None
    namespace: Optional[str] = None
    description: Optional[str] = None
    project: Optional[dict[str, Any]] = None
    labels: Optional[dict[str, Any]] = None
    annotations: Optional[dict[str, Any]] = None
    icon: Optional[str] = None


class GetAppStatusOutput(_StructuredOutput):
    """vela_get_app_status 的结构化输出（status 可为列表或字典）"""

    status: Optional[Any] = None


class ListProjectsOutput(_StructuredOutput):
    """vela_list_projects 的结构化输出"""

    projects: Optional[list[dict[str, Any]]] = None


# ==================== 只读工具 ====================
@mcp.tool(
    name="vela_list_applications",
    annotations=_ro("列出 KubeVela 应用"),
    structured_output=True,
)
async def vela_list_applications(
    query: Annotated[Optional[str], Field(default=None, description="按名称/别名/描述模糊过滤")] = None,
    project_name: Annotated[Optional[str], Field(default=None, description="按项目过滤")] = None,
    env: Annotated[Optional[str], Field(default=None, description="按环境过滤")] = None,
    target_name: Annotated[Optional[str], Field(default=None, description="按交付目标过滤")] = None,
    response_format: Annotated[
        ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="输出格式：markdown 或 json")
    ] = ResponseFormat.MARKDOWN,
) -> Annotated[CallToolResult, ListApplicationsOutput]:
    """列出 KubeVela 应用，支持按项目/环境/交付目标/关键字过滤。

    对应 API：GET /api/v1/applications
    """
    try:
        client = await get_vela_client()
        data = await client.list_applications(
            query=query,
            project=project_name,
            env=env,
            target_name=target_name,
        )
        if response_format == ResponseFormat.JSON:
            markdown = to_json(data)
        else:
            markdown = render_list(
                "应用列表",
                data.get("applications") or [],
                [
                    ("name", "名称"),
                    ("alias", "别名"),
                    ("project", "项目"),
                    ("description", "描述"),
                    ("createTime", "创建时间"),
                ],
            )
        return CallToolResult(
            content=[TextContent(text=markdown)],
            structured_content=data,
        )
    except Exception as e:
        return CallToolResult(content=[TextContent(text=handle_error(e))])


@mcp.tool(
    name="vela_get_application",
    annotations=_ro("查看应用详情"),
    structured_output=True,
)
async def vela_get_application(
    app_name: Annotated[str, Field(description="应用名称", min_length=1, max_length=64)],
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> Annotated[CallToolResult, GetApplicationOutput]:
    """查看应用详情（基础信息、策略、环境绑定、资源统计）。

    对应 API：GET /api/v1/applications/{appName}
    """
    try:
        client = await get_vela_client()
        data = await client.get_application(app_name)
        if response_format == ResponseFormat.JSON:
            markdown = to_json(data)
        else:
            markdown = render_kv(f"应用：{app_name}", data)
        return CallToolResult(
            content=[TextContent(text=markdown)],
            structured_content=data,
        )
    except Exception as e:
        return CallToolResult(content=[TextContent(text=handle_error(e))])


@mcp.tool(
    name="vela_get_app_status",
    annotations=_ro("查看应用运行状态"),
    structured_output=True,
)
async def vela_get_app_status(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    env: Annotated[Optional[str], Field(default=None, description="环境名。不填返回所有环境的状态概览")] = None,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> Annotated[CallToolResult, GetAppStatusOutput]:
    """查看应用运行状态（全部环境概览或指定环境详情）。

    对应 API：GET /api/v1/applications/{app}/status
             GET /api/v1/applications/{app}/envs/{env}/status
    """
    try:
        client = await get_vela_client()
        data = await client.get_application_status(app_name, env=env)
        if response_format == ResponseFormat.JSON:
            markdown = to_json(data)
        elif env:
            status = data.get("status") or {}
            lines = [
                f"# 应用 {app_name} @ {env} 状态",
                "",
                f"- 阶段：{fmt_status(status.get('status'))}",
            ]
            services = status.get("services") or []
            if services:
                lines += ["", "## 组件状态", ""]
                lines.append(render_list(
                    "", services,
                    [("name", "组件"), ("healthy", "健康"), ("message", "信息")],
                ))
            conditions = status.get("conditions") or []
            if conditions:
                lines += ["", "## Conditions", ""]
                lines.append(render_list(
                    "", conditions,
                    [("type", "类型"), ("status", "状态"), ("reason", "原因")],
                ))
            markdown = "\n".join(lines)
        else:
            rows = [
                {"envName": s.get("envName"), "status": (s.get("status") or {}).get("status")}
                for s in (data.get("status") or [])
            ]
            markdown = render_list(
                f"应用 {app_name} 各环境状态",
                rows,
                [("envName", "环境"), ("status", "状态")],
            )
        return CallToolResult(
            content=[TextContent(text=markdown)],
            structured_content=data,
        )
    except Exception as e:
        return CallToolResult(content=[TextContent(text=handle_error(e))])


@mcp.tool(name="vela_list_components", annotations=_ro("查看应用组件"))
async def vela_list_components(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    component: Annotated[
        Optional[str], Field(default=None, description="组件名。填写后返回该组件详情（含 properties 与 traits）")
    ] = None,
    env: Annotated[Optional[str], Field(default=None, description="环境名（仅列表时生效）")] = None,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看应用的组件列表；指定 component 时返回组件详情（properties、traits、definition）。

    对应 API：GET /api/v1/applications/{app}/components[/{comp}]
    """
    try:
        client = await get_vela_client()
        if component:
            data = await client.get_component(app_name, component)
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_kv(f"组件：{app_name}/{component}", data)
        data = await client.list_components(app_name, env=env)
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {app_name} 组件列表",
            data.get("components") or [],
            [
                ("name", "名称"),
                ("componentType", "类型"),
                ("main", "主组件"),
                ("createTime", "创建时间"),
            ],
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_revisions", annotations=_ro("查看应用版本历史"))
async def vela_list_revisions(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    env: Annotated[Optional[str], Field(default=None, description="按环境过滤")] = None,
    status: Annotated[
        Optional[str], Field(default=None, description="按状态过滤，如 complete/failed/terminated")
    ] = None,
    page: Annotated[int, Field(default=0, ge=0, description="页码（从 0 开始）")] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="分页大小")] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看应用版本（revision）历史，可用于回滚前确认目标版本。

    对应 API：GET /api/v1/applications/{app}/revisions
    """
    try:
        client = await get_vela_client()
        data = await client.list_revisions(
            app_name,
            env=env,
            status=status,
            page=page,
            page_size=page_size,
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {app_name} 版本历史",
            data.get("revisions") or [],
            [
                ("version", "版本"),
                ("status", "状态"),
                ("envName", "环境"),
                ("triggerType", "触发方式"),
                ("createTime", "创建时间"),
                ("note", "说明"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_deploy_records", annotations=_ro("查看环境部署记录"))
async def vela_list_deploy_records(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    env: Annotated[str, Field(description="环境名", min_length=1)],
    page: Annotated[int, Field(default=0, ge=0)] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看应用在指定环境的部署记录。

    对应 API：GET /api/v1/applications/{app}/envs/{env}/records
    """
    try:
        client = await get_vela_client()
        data = await client.list_deploy_records(
            app_name, env, page=page, page_size=page_size
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {app_name} @ {env} 部署记录",
            data.get("records") or [],
            [
                ("name", "记录名"),
                ("workflowName", "工作流"),
                ("status", "状态"),
                ("applicationRevision", "版本"),
                ("startTime", "开始时间"),
                ("message", "信息"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_workflow_records", annotations=_ro("查看工作流与执行记录"))
async def vela_list_workflow_records(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    workflow_name: Annotated[
        Optional[str], Field(default=None, description="工作流名（形如 workflow-<env>）。不填则返回该应用的工作流列表")
    ] = None,
    record: Annotated[
        Optional[str], Field(default=None, description="记录名。填写后返回该次执行的详情（含步骤状态）")
    ] = None,
    page: Annotated[int, Field(default=0, ge=0)] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """三合一查询：不填 workflow_name 列出工作流；填 workflow_name 列出执行记录；
    再填 record 返回该次执行详情（含各步骤状态，可据此取日志）。

    对应 API：GET /api/v1/applications/{app}/workflows[/{wf}/records[/{record}]]
    """
    try:
        client = await get_vela_client()
        if not workflow_name:
            data = await client.list_workflows(app_name)
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_list(
                f"应用 {app_name} 工作流列表",
                data.get("workflows") or [],
                [
                    ("name", "名称"),
                    ("envName", "环境"),
                    ("default", "默认"),
                    ("mode", "模式"),
                    ("createTime", "创建时间"),
                ],
            )
        if record:
            data = await client.get_workflow_record(
                app_name, workflow_name, record
            )
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            steps = data.get("steps") or []
            lines = [render_kv(f"执行记录：{record}",
                               {k: v for k, v in data.items() if k != "steps"})]
            if steps:
                lines += ["", render_list(
                    "步骤", steps,
                    [("name", "步骤"), ("type", "类型"), ("phase", "阶段"), ("message", "信息")],
                )]
            return "\n".join(lines)
        data = await client.list_workflow_records(
            app_name, workflow_name,
            page=page, page_size=page_size,
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"工作流 {workflow_name} 执行记录",
            data.get("records") or [],
            [
                ("name", "记录名"),
                ("status", "状态"),
                ("applicationRevision", "版本"),
                ("startTime", "开始时间"),
                ("endTime", "结束时间"),
                ("message", "信息"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_get_workflow_logs", annotations=_ro("查看工作流步骤日志"))
async def vela_get_workflow_logs(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    workflow_name: Annotated[str, Field(description="工作流名", min_length=1)],
    record: Annotated[str, Field(description="工作流执行记录名", min_length=1)],
    step: Annotated[str, Field(description="步骤名（必填，来自记录详情中的步骤列表）")],
) -> str:
    """查看工作流执行记录中某个步骤的日志（step 必填，步骤名可先查记录详情获取）。

    对应 API：GET .../workflows/{wf}/records/{record}/logs?step=
    """
    try:
        client = await get_vela_client()
        data = await client.get_workflow_record_logs(
            app_name, workflow_name, record, step
        )
        log = data.get("log") or "（无日志）"
        return (
            f"# 步骤 {step} 日志\n\n"
            f"记录：{record}（来源：{data.get('source') or '-'}）\n\n"
            f"```\n{log}\n```"
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_envs", annotations=_ro("列出环境"))
async def vela_list_envs(
    project_name: Annotated[Optional[str], Field(default=None, description="按项目过滤")] = None,
    page: Annotated[int, Field(default=0, ge=0)] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """列出环境（env）及其关联的交付目标。

    对应 API：GET /api/v1/envs
    """
    try:
        client = await get_vela_client()
        data = await client.list_envs(
            project=project_name, page=page, page_size=page_size
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        rows = []
        for env in data.get("envs") or []:
            rows.append({
                "name": env.get("name"),
                "alias": env.get("alias"),
                "project": (env.get("project") or {}).get("name"),
                "namespace": env.get("namespace"),
                "targets": ",".join(
                    t.get("name", "") for t in (env.get("targets") or [])
                ),
            })
        return render_list(
            "环境列表",
            rows,
            [
                ("name", "名称"),
                ("alias", "别名"),
                ("project", "项目"),
                ("namespace", "命名空间"),
                ("targets", "交付目标"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_targets", annotations=_ro("列出交付目标"))
async def vela_list_targets(
    project_name: Annotated[Optional[str], Field(default=None, description="按项目过滤")] = None,
    page: Annotated[int, Field(default=0, ge=0)] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """列出交付目标（target，即集群+命名空间组合）。

    对应 API：GET /api/v1/targets
    """
    try:
        client = await get_vela_client()
        data = await client.list_targets(
            project=project_name, page=page, page_size=page_size
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        rows = []
        for t in data.get("targets") or []:
            cluster = (t.get("cluster") or {})
            rows.append({
                "name": t.get("name"),
                "alias": t.get("alias"),
                "clusterName": cluster.get("clusterName"),
                "namespace": cluster.get("namespace"),
                "description": t.get("description"),
            })
        return render_list(
            "交付目标列表",
            rows,
            [
                ("name", "名称"),
                ("alias", "别名"),
                ("clusterName", "集群"),
                ("namespace", "命名空间"),
                ("description", "描述"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_clusters", annotations=_ro("列出集群"))
async def vela_list_clusters(
    cluster_name: Annotated[Optional[str], Field(default=None, description="集群名。填写后返回该集群详情")] = None,
    query: Annotated[Optional[str], Field(default=None, description="按名称模糊过滤（仅列表时生效）")] = None,
    page: Annotated[int, Field(default=0, ge=0)] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100)] = 20,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """列出纳管集群；指定 cluster_name 时返回集群详情（含资源信息）。

    对应 API：GET /api/v1/clusters[/{clusterName}]
    """
    try:
        client = await get_vela_client()
        if cluster_name:
            data = await client.get_cluster(cluster_name)
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_kv(f"集群：{cluster_name}", data)
        data = await client.list_clusters(
            query=query, page=page, page_size=page_size
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            "集群列表",
            data.get("clusters") or [],
            [
                ("name", "名称"),
                ("alias", "别名"),
                ("status", "状态"),
                ("apiServerURL", "APIServer"),
                ("description", "描述"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_addons", annotations=_ro("查看插件"))
async def vela_list_addons(
    addon_name: Annotated[
        Optional[str], Field(default=None, description="插件名。填写后返回该插件详情与启用状态")
    ] = None,
    registry: Annotated[Optional[str], Field(default=None, description="按插件仓库过滤")] = None,
    query: Annotated[Optional[str], Field(default=None, description="按名称模糊过滤")] = None,
    enabled_only: Annotated[bool, Field(default=False, description="仅列出已启用的插件")] = False,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看插件（addon）市场列表 / 已启用插件 / 单个插件详情与状态。

    对应 API：GET /api/v1/addons[...]、GET /api/v1/enabled_addon
    """
    try:
        client = await get_vela_client()
        if addon_name:
            detail = await client.get_addon(addon_name, registry=registry)
            status = await client.get_addon_status(addon_name)
            if response_format == ResponseFormat.JSON:
                return to_json({"detail": detail, "status": status})
            merged = {
                "name": detail.get("name") or addon_name,
                "version": detail.get("version"),
                "description": detail.get("description"),
                "registryName": detail.get("registryName"),
                "availableVersions": detail.get("availableVersions"),
                "phase": status.get("phase"),
                "installedVersion": status.get("installedVersion"),
                "clusters": status.get("clusters"),
            }
            return render_kv(f"插件：{addon_name}", merged)
        if enabled_only:
            data = await client.list_enabled_addons()
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_list(
                "已启用插件",
                data.get("enabledAddons") or [],
                [("name", "名称"), ("phase", "状态")],
            )
        data = await client.list_addons(registry=registry, query=query)
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        rows = []
        for a in data.get("addons") or []:
            rows.append({
                "name": a.get("name"),
                "version": a.get("version"),
                "description": a.get("description"),
                "registryName": a.get("registryName"),
            })
        return render_list(
            "插件市场",
            rows,
            [
                ("name", "名称"),
                ("version", "版本"),
                ("registryName", "仓库"),
                ("description", "描述"),
            ],
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_definitions", annotations=_ro("查看 X-Definition"))
async def vela_list_definitions(
    def_type: Annotated[DefinitionType, Field(description="定义类型：component / trait / policy / workflowstep")],
    definition_name: Annotated[
        Optional[str], Field(default=None, description="定义名。填写后返回详情（含参数 schema）")
    ] = None,
    query_all: Annotated[bool, Field(default=False, description="是否包含隐藏定义（默认只列常用定义）")] = False,
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看组件/运维特征/策略/工作流步骤定义；指定 definition_name 返回参数 schema。

    对应 API：GET /api/v1/definitions[/{name}]?type=
    """
    try:
        client = await get_vela_client()
        if definition_name:
            data = await client.get_definition(
                definition_name, def_type.value
            )
            if response_format == ResponseFormat.JSON:
                return to_json(data)
            schema = data.get("schema")
            lines = [render_kv(
                f"定义：{definition_name}（{def_type.value}）",
                {k: v for k, v in data.items() if k not in ("schema", "uiSchema")},
            )]
            if schema:
                lines += ["", "## 参数 Schema", "", f"```json\n{to_json(schema)}\n```"]
            return "\n".join(lines)
        data = await client.list_definitions(
            def_type.value, query_all=query_all
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"{def_type.value} 定义列表",
            data.get("definitions") or [],
            [("name", "名称"), ("description", "描述"), ("ownerAddon", "所属插件")],
        )
    except Exception as e:
        return handle_error(e)


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



@mcp.tool(name="vela_system_info", annotations=_ro("查看平台系统信息"))
async def vela_system_info(
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """查看 VelaUX 平台系统信息：KubeVela 版本、登录方式、集群/应用统计、已启用插件等。

    适合作为接入新环境后的连通性与版本自检。
    对应 API：GET /api/v1/system_info
    """
    try:
        client = await get_vela_client()
        data = await client.get_system_info()
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        version = data.get("systemVersion") or {}
        stat = data.get("statisticInfo") or {}
        info: dict[str, Any] = {
            "KubeVela 版本": version.get("velaVersion"),
            "Git 版本": version.get("gitVersion"),
            "登录方式": data.get("loginType"),
            "平台 ID": data.get("platformID"),
            "安装时间": data.get("installTime"),
            "集群数": stat.get("clusterCount"),
            "应用数": stat.get("appCount"),
            "统计更新时间": stat.get("updateTime"),
        }
        lines = [render_kv("VelaUX 系统信息", info)]
        addons = stat.get("enableAddonList") or {}
        if addons:
            lines += [
                "",
                "## 已启用插件",
                "",
                *(f"- {name}：{ver}" for name, ver in sorted(addons.items())),
            ]
        return "\n".join(lines)
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_compare_application", annotations=_ro("对比应用配置差异"))
async def vela_compare_application(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    env: Annotated[
        Optional[str], Field(default=None, description="环境名。不传 revision 时必填：对比最新配置与该环境集群运行态")
    ] = None,
    revision: Annotated[Optional[str], Field(default=None, description="版本号。传入后以该版本为基准做对比")] = None,
    compare_with: Annotated[
        str,
        Field(
            default="running",
            description="revision 的对比对象：running（集群运行态）或 latest（最新配置）",
            pattern="^(running|latest)$",
        ),
    ] = "running",
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """对比应用配置差异（诊断配置漂移）。三种模式：

    - 仅传 env：最新配置 vs 该环境集群运行态
    - 传 revision + compare_with=running：指定版本 vs 集群运行态
    - 传 revision + compare_with=latest：指定版本 vs 最新配置

    对应 API：POST /api/v1/applications/{app}/compare
    """
    try:
        if not revision and not env:
            return "错误：env 与 revision 至少需要提供一个"
        client = await get_vela_client()
        data = await client.compare_application(
            app_name,
            env=env,
            revision=revision,
            compare_with=compare_with,
        )
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        is_diff = data.get("isDiff")
        lines = [
            f"# 应用对比：{app_name}",
            "",
            f"**是否存在差异**：{'⚠️ 有差异' if is_diff else '✅ 无差异'}",
        ]
        report = (data.get("diffReport") or "").strip()
        if report:
            lines += ["", "## 差异报告", "", "```diff", report, "```"]
        elif is_diff and not data.get("baseAppYAML"):
            lines += ["", "> 注：基准侧为空（如环境尚未部署过），仅目标侧存在配置。"]
        return "\n".join(lines)
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_get_application_manifest", annotations=_ro("导出应用 YAML 清单"))
async def vela_get_application_manifest(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    env: Annotated[str, Field(description="环境名", min_length=1)],
    source: Annotated[
        str,
        Field(
            default="latest",
            description="导出来源：latest（最新渲染配置）或 running（集群运行态 CR）",
            pattern="^(latest|running)$",
        ),
    ] = "latest",
) -> str:
    """导出应用的 Application CR YAML 清单（GitOps 迁移 / 备份 / 审计用）。

    source=latest 导出最新渲染配置；source=running 导出集群中实际运行的 CR。
    基于 compare 接口的 YAML 字段实现。
    对应 API：POST /api/v1/applications/{app}/compare
    """
    try:
        client = await get_vela_client()
        data = await client.compare_application(app_name, env=env)
        # compareLatestWithRunning：base=集群运行态，target=最新渲染配置
        yaml_text = (
            data.get("targetAppYAML")
            if source == "latest"
            else data.get("baseAppYAML")
        ) or ""
        yaml_text = yaml_text.strip()
        if not yaml_text:
            hint = (
                "集群运行态为空（应用可能尚未部署到该环境）"
                if source == "running"
                else "最新配置渲染为空，请检查应用与环境绑定"
            )
            return f"错误：未获取到 YAML 清单，{hint}"
        return (
            f"# 应用清单：{app_name}（env={env}，source={source}）\n\n"
            f"```yaml\n{yaml_text}\n```"
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_triggers", annotations=_ro("查看应用触发器"))
async def vela_list_triggers(
    app_name: Annotated[str, Field(description="应用名称", min_length=1)],
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """列出应用的 webhook 触发器（含 token，可拼接触发地址）。

    对应 API：GET /api/v1/applications/{app}/triggers
    """
    try:
        client = await get_vela_client()
        data = await client.list_triggers(app_name)
        triggers = data.get("triggers") or []
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        table = render_list(
            f"应用触发器：{app_name}",
            triggers,
            columns=[
                ("name", "名称"),
                ("type", "类型"),
                ("payloadType", "载荷类型"),
                ("workflowName", "工作流"),
                ("componentName", "组件"),
                ("token", "Token"),
                ("createTime", "创建时间"),
            ],
        )
        return table + (
            "\n\n> 触发地址：POST {VELA_URL}/api/v1/webhook/{token}" if triggers else ""
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(
    name="vela_list_projects",
    annotations=_ro("列出项目"),
    structured_output=True,
)
async def vela_list_projects(
    page: Annotated[int, Field(default=0, ge=0, description="页码（从 0 开始）")] = 0,
    page_size: Annotated[int, Field(default=20, ge=1, le=100, description="分页大小")] = 20,
    response_format: Annotated[
        ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="输出格式：markdown 或 json")
    ] = ResponseFormat.MARKDOWN,
) -> Annotated[CallToolResult, ListProjectsOutput]:
    """列出平台中的项目（project），创建应用前用于确认可选项目。

    对应 API：GET /api/v1/projects
    """
    try:
        client = await get_vela_client()
        data = await client.list_projects(
            page=page, page_size=page_size
        )
        if response_format == ResponseFormat.JSON:
            markdown = to_json(data)
        else:
            rows = []
            for p in data.get("projects") or []:
                rows.append({
                    "name": p.get("name"),
                    "alias": p.get("alias"),
                    "namespace": p.get("namespace"),
                    "owner": (p.get("owner") or {}).get("name"),
                    "description": p.get("description"),
                    "createTime": p.get("createTime"),
                })
            markdown = render_list(
                "项目列表",
                rows,
                [
                    ("name", "名称"),
                    ("alias", "别名"),
                    ("namespace", "命名空间"),
                    ("owner", "负责人"),
                    ("description", "描述"),
                    ("createTime", "创建时间"),
                ],
                total=data.get("total"),
            )
        return CallToolResult(
            content=[TextContent(text=markdown)],
            structured_content=data,
        )
    except Exception as e:
        return CallToolResult(content=[TextContent(text=handle_error(e))])


@mcp.tool(name="vela_list_project_targets", annotations=_ro("列出项目交付目标"))
async def vela_list_project_targets(
    project_name: Annotated[str, Field(description="项目名称", min_length=1)],
    response_format: Annotated[
        ResponseFormat, Field(default=ResponseFormat.MARKDOWN, description="输出格式：markdown 或 json")
    ] = ResponseFormat.MARKDOWN,
) -> str:
    """列出指定项目可用的交付目标（target），部署前确认目标合法性。

    对应 API：GET /api/v1/projects/{projectName}/targets
    """
    try:
        client = await get_vela_client()
        data = await client.list_project_targets(project_name)
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        rows = []
        for t in data.get("targets") or []:
            cluster = t.get("cluster") or {}
            rows.append({
                "name": t.get("name"),
                "alias": t.get("alias"),
                "clusterName": cluster.get("clusterName"),
                "namespace": cluster.get("namespace"),
                "description": t.get("description"),
            })
        return render_list(
            f"项目 {project_name} 的交付目标",
            rows,
            [
                ("name", "名称"),
                ("alias", "别名"),
                ("clusterName", "集群"),
                ("namespace", "命名空间"),
                ("description", "描述"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_project_users", annotations=_ro("列出项目成员"))
async def vela_list_project_users(
    project_name: Annotated[str, Field(description="项目名称", min_length=1)],
    response_format: Annotated[ResponseFormat, Field(default=ResponseFormat.MARKDOWN)] = ResponseFormat.MARKDOWN,
) -> str:
    """列出指定项目的成员及其角色，用于排查权限（403）类问题。

    对应 API：GET /api/v1/projects/{projectName}/users
    """
    try:
        client = await get_vela_client()
        data = await client.list_project_users(project_name)
        if response_format == ResponseFormat.JSON:
            return to_json(data)
        rows = []
        for u in data.get("users") or []:
            rows.append({
                "name": u.get("name"),
                "alias": u.get("alias"),
                "userRoles": ",".join(u.get("userRoles") or []),
                "createTime": u.get("createTime"),
            })
        return render_list(
            f"项目 {project_name} 的成员",
            rows,
            [
                ("name", "用户名"),
                ("alias", "别名"),
                ("userRoles", "项目角色"),
                ("createTime", "加入时间"),
            ],
            total=data.get("total"),
        )
    except Exception as e:
        return handle_error(e)


# ==================== MCP Resources：只读元数据 ====================
@mcp.resource("vela://system-info")
async def resource_system_info() -> str:
    """VelaUX 平台系统信息（只读资源）"""
    try:
        client = await get_vela_client()
        data = await client.get_system_info()
        return to_json(data)
    except Exception as e:
        return handle_error(e)


@mcp.resource("vela://projects")
async def resource_projects() -> str:
    """列出所有项目（只读资源）"""
    try:
        client = await get_vela_client()
        data = await client.list_projects(page=0, page_size=100)
        return to_json(data)
    except Exception as e:
        return handle_error(e)


@mcp.resource("vela://envs")
async def resource_envs() -> str:
    """列出所有环境（只读资源）"""
    try:
        client = await get_vela_client()
        data = await client.list_envs(project=None, page=0, page_size=100)
        return to_json(data)
    except Exception as e:
        return handle_error(e)


@mcp.resource("vela://clusters")
async def resource_clusters() -> str:
    """列出所有纳管集群（只读资源）"""
    try:
        client = await get_vela_client()
        data = await client.list_clusters(query=None, page=0, page_size=100)
        return to_json(data)
    except Exception as e:
        return handle_error(e)


# ==================== 写工具（只读模式下不注册） ====================
_read_only = os.getenv("VELA_READ_ONLY", "false").lower() == "true"

if not _read_only:

    @mcp.tool(name="vela_create_application", annotations=_rw("创建 KubeVela 应用"))
    async def vela_create_application(
        name: Annotated[str, Field(description="应用名称（小写字母/数字/中划线）", min_length=1)],
        project_name: Annotated[str, Field(description="所属项目（应用创建后归属的项目，必填）", min_length=1)],
        component_name: Annotated[str, Field(description="首个组件的名称", min_length=1)],
        component_type: Annotated[
            str, Field(description="组件类型（ComponentDefinition 名），如 webservice、helm", min_length=1)
        ],
        properties: Annotated[
            Optional[str],
            Field(
                default=None,
                description='组件 properties，JSON 字符串，如 {"image":"nginx:latest","port":80}'
            ),
        ] = None,
        alias: Annotated[Optional[str], Field(default=None, description="应用别名")] = None,
        description: Annotated[Optional[str], Field(default=None, description="应用描述")] = None,
        env_binding: Annotated[
            Optional[list[str]], Field(default=None, description='绑定的环境名列表，如 ["dev","prod"]')
        ] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """创建应用（含首个组件），可同时绑定环境。

        对应 API：POST /api/v1/applications
        """
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在创建应用 {name}...")
            client = await get_vela_client()
            component: dict[str, Any] = {
                "name": component_name,
                "componentType": component_type,
            }
            if properties:
                component["properties"] = properties
            data = await client.create_application(
                name=name,
                project=project_name,
                component=component,
                alias=alias,
                description=description,
                env_binding=env_binding,
            )
            return render_kv("应用创建成功", data) + (
                "\n\n> 提示：应用尚未部署，可调用 vela_deploy_application 触发部署。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_deploy_application", annotations=_rw("部署应用"))
    async def vela_deploy_application(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        workflow_name: Annotated[
            Optional[str],
            Field(
                default=None,
                description="工作流名（对应环境，形如 workflow-<env>）。不填使用默认工作流"
            ),
        ] = None,
        note: Annotated[Optional[str], Field(default=None, description="部署说明")] = None,
        force: Annotated[bool, Field(default=False, description="是否强制部署（存在执行中的工作流时覆盖）")] = False,
        ctx: Optional[Context] = None,
    ) -> str:
        """触发应用部署工作流（异步）。返回执行记录名，可用 vela_list_workflow_records 跟踪进度。

        对应 API：POST /api/v1/applications/{app}/deploy
        """
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在触发应用 {app_name} 部署...")
            client = await get_vela_client()
            data = await client.deploy_application(
                app_name,
                workflow_name=workflow_name,
                note=note,
                force=force,
            )
            record = data.get("record") or {}
            if ctx:
                await ctx.report_progress(0, 0, f"部署已触发，执行记录：{record.get('name')}")
            lines = [
                "# 部署已触发",
                "",
                "| 属性 | 值 |",
                "|------|-----|",
                f"| 应用 | {app_name} |",
                f"| 版本 | {data.get('version')} |",
                f"| 工作流 | {record.get('workflowName') or workflow_name or '-'} |",
                f"| 执行记录 | {record.get('name')} |",
                f"| 状态 | {fmt_status(data.get('status') or record.get('status'))} |",
                "",
                "> 提示：部署为异步执行，可调用 vela_list_workflow_records"
                "（传入 workflow_name 与 record）跟踪进度与步骤状态。",
            ]
            return "\n".join(lines)
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_dry_run_application", annotations=_rw("部署预演 Dry-Run", idempotent=True))
    async def vela_dry_run_application(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        env: Annotated[Optional[str], Field(default=None, description="目标环境")] = None,
        workflow: Annotated[Optional[str], Field(default=None, description="工作流名")] = None,
        version: Annotated[
            Optional[str], Field(default=None, description="指定版本号则按 REVISION 预演，否则按当前配置（APP）预演")
        ] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """部署预演：渲染出将要下发的 K8s 资源 YAML，但不实际部署（安全）。

        对应 API：POST /api/v1/applications/{app}/dry-run
        """
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在预演应用 {app_name}...")
            client = await get_vela_client()
            data = await client.dry_run_application(
                app_name,
                env=env,
                workflow=workflow,
                version=version,
            )
            ok = data.get("success")
            head = "# Dry-Run 成功 ✅" if ok else f"# Dry-Run 失败 ❌\n\n{data.get('message')}"
            yaml_text = data.get("yaml") or ""
            if yaml_text:
                head += f"\n\n```yaml\n{yaml_text}\n```"
            return head
        except Exception as e:
            return handle_error(e)

    @mcp.tool(
        name="vela_rollback_application",
        annotations=_rw("回滚应用版本", destructive=True),
    )
    async def vela_rollback_application(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        revision: Annotated[str, Field(description="目标版本号（来自版本历史）", min_length=1)],
        ctx: Optional[Context] = None,
    ) -> str:
        """回滚应用到指定历史版本（危险操作，MRTR 确认）。

        对应 API：POST /api/v1/applications/{app}/revisions/{revision}/rollback
        """
        if ctx:
            proceed, msg = await _confirm_action(ctx, f"回滚应用 {app_name} 到版本 {revision}")
            if not proceed:
                return msg
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在回滚应用 {app_name} 到版本 {revision}...")
            client = await get_vela_client()
            data = await client.rollback_application(app_name, revision)
            record = data.get("record") or {}
            return (
                f"# 回滚已触发\n\n应用 {app_name} → 版本 {revision}\n\n"
                f"执行记录：{record.get('name')}（工作流 {record.get('workflowName')}）\n\n"
                "> 提示：可调用 vela_list_workflow_records 跟踪回滚进度。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_resume_workflow", annotations=_rw("恢复挂起的工作流"))
    async def vela_resume_workflow(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        workflow_name: Annotated[str, Field(description="工作流名", min_length=1)],
        record: Annotated[str, Field(description="工作流执行记录名", min_length=1)],
        step: Annotated[Optional[str], Field(default=None, description="步骤名（仅恢复指定步骤时填写）")] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """恢复挂起（suspend）的工作流，常用于人工审批后继续发布。

        对应 API：GET .../records/{record}/resume
        """
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在恢复工作流 {app_name}/{workflow_name}...")
            client = await get_vela_client()
            await client.resume_workflow_record(
                app_name, workflow_name, record, step=step
            )
            return (
                f"工作流已恢复：{app_name}/{workflow_name}/{record}\n\n"
                "> 提示：可调用 vela_list_workflow_records 跟踪后续进度。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(
        name="vela_terminate_workflow",
        annotations=_rw("终止执行中的工作流", destructive=True),
    )
    async def vela_terminate_workflow(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        workflow_name: Annotated[str, Field(description="工作流名", min_length=1)],
        record: Annotated[str, Field(description="工作流执行记录名", min_length=1)],
        ctx: Optional[Context] = None,
    ) -> str:
        """终止执行中的工作流（危险操作，MRTR 确认）。

        对应 API：GET .../records/{record}/terminate
        """
        if ctx:
            proceed, msg = await _confirm_action(ctx, f"终止工作流 {app_name}/{workflow_name}/{record}")
            if not proceed:
                return msg
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在终止工作流 {app_name}/{workflow_name}...")
            client = await get_vela_client()
            await client.terminate_workflow_record(
                app_name, workflow_name, record
            )
            return f"工作流已终止：{app_name}/{workflow_name}/{record}"
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_create_trigger", annotations=_rw("创建应用触发器"))
    async def vela_create_trigger(
        app_name: Annotated[str, Field(description="应用名称", min_length=1)],
        name: Annotated[str, Field(description="触发器名称", min_length=1)],
        workflow_name: Annotated[str, Field(description="触发的工作流名", min_length=1)],
        payload_type: Annotated[
            str,
            Field(
                default="custom",
                description="载荷类型：custom / dockerhub / acr / harbor / jfrog",
                pattern="^(custom|dockerhub|acr|harbor|jfrog)$",
            ),
        ] = "custom",
        component_name: Annotated[
            Optional[str], Field(default=None, description="镜像类载荷要更新的组件名（可选）")
        ] = None,
        registry: Annotated[Optional[str], Field(default=None, description="镜像仓库（可选）")] = None,
        description: Annotated[Optional[str], Field(default=None, description="描述（可选）")] = None,
        ctx: Optional[Context] = None,
    ) -> str:
        """创建应用的 webhook 触发器，payload_type 支持
        custom / dockerhub / acr / harbor / jfrog，返回 token。

        对应 API：POST /api/v1/applications/{app}/triggers
        """
        try:
            if ctx:
                await ctx.report_progress(0, 0, f"正在创建触发器 {name}...")
            client = await get_vela_client()
            data = await client.create_trigger(
                app_name,
                name=name,
                workflow_name=workflow_name,
                payload_type=payload_type,
                description=description,
                component_name=component_name,
                registry=registry,
            )
            token = data.get("token", "")
            return render_kv("触发器创建成功", data) + (
                f"\n\n> 触发地址：POST {{VELA_URL}}/api/v1/webhook/{token}"
            )
        except Exception as e:
            return handle_error(e)


# ==================== 传输与入口 ====================
def _normalize_transport(raw: Optional[str]) -> str:
    """规范化 MCP_TRANSPORT，支持 stdio（默认）、sse、streamable-http。"""
    value = (raw or "stdio").strip().lower().replace("_", "-")
    if value == "stdio":
        return "stdio"
    if value == "sse":
        return "sse"
    if value in {"streamable-http", "streamablehttp", "http"}:
        return "streamable-http"
    raise ValueError(
        f"不支持的 MCP_TRANSPORT: {raw!r}，仅支持 stdio、sse、streamable-http"
    )


def _run_http(transport: str) -> None:
    """以 HTTP 方式（sse / streamable-http）运行，并按需启用 Token 认证。"""
    import uvicorn

    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8080"))

    if transport == "sse":
        warnings.warn(
            "SSE 传输协议已废弃，建议使用 streamable-http。"
            "设置 MCP_TRANSPORT=streamable-http 以切换。",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning("SSE 传输已废弃，建议切换至 streamable-http")
        app: Any = mcp.sse_app()
        endpoint = "/sse"
    else:
        stateless = os.getenv("MCP_STATELESS_HTTP", "false").lower() == "true"
        app = mcp.streamable_http_app(stateless_http=stateless)
        endpoint = "/mcp"
        if stateless:
            logger.info("已启用 Stateless HTTP 模式（每次请求独立，无会话状态）")

    # 健康检查路由：在鉴权中间件包裹之前挂载，保证无论是否开启鉴权都能探活。
    # MCPServer 原生 app 仅暴露 /mcp（或 /sse），不含 /health。
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def _health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.add_route("/health", _health)

    token = os.getenv("MCP_AUTH_TOKEN")
    if token:
        app = TokenAuthMiddleware(app, token)
        logger.info("HTTP 接口认证已启用（需携带 Authorization: Bearer <token>）")
    else:
        logger.warning(
            "MCP_AUTH_TOKEN 未设置，HTTP 接口处于无鉴权状态，生产环境请务必配置该变量"
        )

    log_level = os.getenv("MCP_LOG_LEVEL", "info").lower()
    logger.info(
        "MCP Server 启动：transport=%s, 监听 http://%s:%s%s",
        transport, host, port, endpoint,
    )
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def main() -> None:
    """MCP Server 入口点。

    通过环境变量 MCP_TRANSPORT 选择传输协议：
        - stdio（默认）：标准输入输出，适合本地 AI 客户端集成
        - sse：Server-Sent Events HTTP 传输
        - streamable-http：Streamable HTTP 传输

    HTTP 传输相关环境变量：
        - MCP_HOST：监听地址，默认 0.0.0.0
        - MCP_PORT：监听端口，默认 8080（避免与 VelaUX 默认 8000 冲突）
        - MCP_AUTH_TOKEN：设置后启用 Bearer Token 认证
        - MCP_LOG_LEVEL：日志级别，默认 info
    """
    logging.basicConfig(
        level=os.getenv("MCP_LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    transport = _normalize_transport(os.getenv("MCP_TRANSPORT"))
    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    _run_http(transport)


if __name__ == "__main__":
    main()
