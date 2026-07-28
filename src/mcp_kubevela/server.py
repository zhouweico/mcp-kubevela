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
from enum import Enum
from typing import Any, Optional, cast

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from .auth import TokenAuthMiddleware
from .client import get_vela_client
from .clients import VelaApiError, VelaAuthError
from .render import fmt_status, render_kv, render_list, to_json

logger = logging.getLogger(__name__)

mcp = FastMCP("kubevela_mcp")

_INPUT_CONFIG = ConfigDict(
    str_strip_whitespace=True,
    validate_assignment=True,
    extra="forbid",
)


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


# ==================== 错误处理 ====================
def handle_error(e: Exception) -> str:
    """统一错误处理：转换为中文可读提示，不抛栈。"""
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


def _ro(title: str) -> Any:
    """只读工具注解"""
    return cast(
        Any,
        {
            "title": title,
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )


def _rw(title: str, destructive: bool = False, idempotent: bool = False) -> Any:
    """写工具注解"""
    return cast(
        Any,
        {
            "title": title,
            "readOnlyHint": False,
            "destructiveHint": destructive,
            "idempotentHint": idempotent,
            "openWorldHint": True,
        },
    )


# ==================== 只读工具入参 ====================
class ListApplicationsInput(BaseModel):
    """列出应用的输入参数"""

    model_config = _INPUT_CONFIG

    query: Optional[str] = Field(default=None, description="按名称/别名/描述模糊过滤")
    project_name: Optional[str] = Field(default=None, description="按项目过滤")
    env: Optional[str] = Field(default=None, description="按环境过滤")
    target_name: Optional[str] = Field(default=None, description="按交付目标过滤")
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN, description="输出格式：markdown 或 json"
    )


class AppNameInput(BaseModel):
    """仅需应用名的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1, max_length=64)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class AppStatusInput(BaseModel):
    """查询应用状态的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: Optional[str] = Field(
        default=None, description="环境名。不填返回所有环境的状态概览"
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListComponentsInput(BaseModel):
    """查询组件的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    component: Optional[str] = Field(
        default=None, description="组件名。填写后返回该组件详情（含 properties 与 traits）"
    )
    env: Optional[str] = Field(default=None, description="环境名（仅列表时生效）")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListRevisionsInput(BaseModel):
    """查询应用版本历史的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: Optional[str] = Field(default=None, description="按环境过滤")
    status: Optional[str] = Field(
        default=None, description="按状态过滤，如 complete/failed/terminated"
    )
    page: int = Field(default=0, ge=0, description="页码（从 0 开始）")
    page_size: int = Field(default=20, ge=1, le=100, description="分页大小")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListDeployRecordsInput(BaseModel):
    """查询环境部署记录的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: str = Field(..., description="环境名", min_length=1)
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListWorkflowRecordsInput(BaseModel):
    """查询工作流/执行记录的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    workflow_name: Optional[str] = Field(
        default=None,
        description="工作流名（形如 workflow-<env>）。不填则返回该应用的工作流列表",
    )
    record: Optional[str] = Field(
        default=None, description="记录名。填写后返回该次执行的详情（含步骤状态）"
    )
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class GetWorkflowLogsInput(BaseModel):
    """查询工作流步骤日志的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    workflow_name: str = Field(..., description="工作流名", min_length=1)
    record: str = Field(..., description="工作流执行记录名", min_length=1)
    step: str = Field(..., description="步骤名（必填，来自记录详情中的步骤列表）")


class ListEnvsInput(BaseModel):
    """列出环境的输入参数"""

    model_config = _INPUT_CONFIG

    project_name: Optional[str] = Field(default=None, description="按项目过滤")
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListTargetsInput(BaseModel):
    """列出交付目标的输入参数"""

    model_config = _INPUT_CONFIG

    project_name: Optional[str] = Field(default=None, description="按项目过滤")
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListClustersInput(BaseModel):
    """列出集群的输入参数"""

    model_config = _INPUT_CONFIG

    cluster_name: Optional[str] = Field(
        default=None, description="集群名。填写后返回该集群详情"
    )
    query: Optional[str] = Field(default=None, description="按名称模糊过滤（仅列表时生效）")
    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListAddonsInput(BaseModel):
    """查询插件的输入参数"""

    model_config = _INPUT_CONFIG

    addon_name: Optional[str] = Field(
        default=None, description="插件名。填写后返回该插件详情与启用状态"
    )
    registry: Optional[str] = Field(default=None, description="按插件仓库过滤")
    query: Optional[str] = Field(default=None, description="按名称模糊过滤")
    enabled_only: bool = Field(default=False, description="仅列出已启用的插件")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class DefinitionType(str, Enum):
    COMPONENT = "component"
    TRAIT = "trait"
    POLICY = "policy"
    WORKFLOWSTEP = "workflowstep"


class ListDefinitionsInput(BaseModel):
    """查询 X-Definition 的输入参数"""

    model_config = _INPUT_CONFIG

    def_type: DefinitionType = Field(
        ..., description="定义类型：component / trait / policy / workflowstep"
    )
    definition_name: Optional[str] = Field(
        default=None, description="定义名。填写后返回详情（含参数 schema）"
    )
    query_all: bool = Field(
        default=False, description="是否包含隐藏定义（默认只列常用定义）"
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class VelaQLInput(BaseModel):
    """VelaQL 查询的输入参数"""

    model_config = _INPUT_CONFIG

    velaql: str = Field(
        ...,
        description=(
            "VelaQL 查询语句，如 "
            'component-pod-view{appNs=default,appName=demo}.status 或 '
            'collect-logs{cluster=local,namespace=default,pod=xxx}.logs'
        ),
        min_length=1,
    )


class ListProjectsInput(BaseModel):
    """列出项目的输入参数"""

    model_config = _INPUT_CONFIG

    page: int = Field(default=0, ge=0)
    page_size: int = Field(default=20, ge=1, le=100)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListProjectTargetsInput(BaseModel):
    """列出项目交付目标的输入参数"""

    model_config = _INPUT_CONFIG

    project_name: str = Field(..., description="项目名称", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListProjectUsersInput(BaseModel):
    """列出项目成员的输入参数"""

    model_config = _INPUT_CONFIG

    project_name: str = Field(..., description="项目名称", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


# ==================== 写工具入参 ====================
class CreateApplicationInput(BaseModel):
    """创建应用的输入参数"""

    model_config = _INPUT_CONFIG

    name: str = Field(..., description="应用名称（小写字母/数字/中划线）", min_length=1)
    project_name: str = Field(..., description="所属项目（应用创建后归属的项目，必填）")
    component_name: str = Field(..., description="首个组件的名称", min_length=1)
    component_type: str = Field(
        ..., description="组件类型（ComponentDefinition 名），如 webservice、helm", min_length=1
    )
    properties: Optional[str] = Field(
        default=None,
        description='组件 properties，JSON 字符串，如 {"image":"nginx:latest","port":80}',
    )
    alias: Optional[str] = Field(default=None, description="应用别名")
    description: Optional[str] = Field(default=None, description="应用描述")
    env_binding: Optional[list[str]] = Field(
        default=None, description="绑定的环境名列表，如 [\"dev\",\"prod\"]"
    )


class DeployApplicationInput(BaseModel):
    """部署应用的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    workflow_name: Optional[str] = Field(
        default=None, description="工作流名（对应环境，形如 workflow-<env>）。不填使用默认工作流"
    )
    note: Optional[str] = Field(default=None, description="部署说明")
    force: bool = Field(
        default=False, description="是否强制部署（存在执行中的工作流时覆盖）"
    )


class DryRunApplicationInput(BaseModel):
    """部署预演的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: Optional[str] = Field(default=None, description="目标环境")
    workflow: Optional[str] = Field(default=None, description="工作流名")
    version: Optional[str] = Field(
        default=None, description="指定版本号则按 REVISION 预演，否则按当前配置（APP）预演"
    )


class RollbackApplicationInput(BaseModel):
    """回滚应用的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    revision: str = Field(..., description="目标版本号（来自版本历史）", min_length=1)
    confirm: bool = Field(
        default=False, description="危险操作确认，必须显式传 true 才会执行"
    )


class ResumeWorkflowInput(BaseModel):
    """恢复工作流的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    workflow_name: str = Field(..., description="工作流名", min_length=1)
    record: str = Field(..., description="工作流执行记录名", min_length=1)
    step: Optional[str] = Field(default=None, description="步骤名（仅恢复指定步骤时填写）")


class TerminateWorkflowInput(BaseModel):
    """终止工作流的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    workflow_name: str = Field(..., description="工作流名", min_length=1)
    record: str = Field(..., description="工作流执行记录名", min_length=1)
    confirm: bool = Field(
        default=False, description="危险操作确认，必须显式传 true 才会执行"
    )


class CompareApplicationInput(BaseModel):
    """对比应用配置差异的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: Optional[str] = Field(
        default=None,
        description="环境名。不传 revision 时必填：对比最新配置与该环境集群运行态",
    )
    revision: Optional[str] = Field(
        default=None, description="版本号。传入后以该版本为基准做对比"
    )
    compare_with: str = Field(
        default="running",
        description="revision 的对比对象：running（集群运行态）或 latest（最新配置）",
        pattern="^(running|latest)$",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class GetManifestInput(BaseModel):
    """导出应用 YAML 清单的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    env: str = Field(..., description="环境名", min_length=1)
    source: str = Field(
        default="latest",
        description="导出来源：latest（最新渲染配置）或 running（集群运行态 CR）",
        pattern="^(latest|running)$",
    )


class ListTriggersInput(BaseModel):
    """列出应用触发器的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class CreateTriggerInput(BaseModel):
    """创建应用 webhook 触发器的输入参数"""

    model_config = _INPUT_CONFIG

    app_name: str = Field(..., description="应用名称", min_length=1)
    name: str = Field(..., description="触发器名称", min_length=1)
    workflow_name: str = Field(..., description="触发的工作流名", min_length=1)
    payload_type: str = Field(
        default="custom",
        description="载荷类型：custom / dockerhub / acr / harbor / jfrog",
        pattern="^(custom|dockerhub|acr|harbor|jfrog)$",
    )
    component_name: Optional[str] = Field(
        default=None, description="镜像类载荷要更新的组件名（可选）"
    )
    registry: Optional[str] = Field(default=None, description="镜像仓库（可选）")
    description: Optional[str] = Field(default=None, description="描述（可选）")


_CONFIRM_HINT = "已取消：该操作有风险，请在确认后携带 confirm=true 重新调用。"


# ==================== 只读工具 ====================
@mcp.tool(name="vela_list_applications", annotations=_ro("列出 KubeVela 应用"))
async def vela_list_applications(params: ListApplicationsInput) -> str:
    """列出 KubeVela 应用，支持按项目/环境/交付目标/关键字过滤。

    对应 API：GET /api/v1/applications
    """
    try:
        client = await get_vela_client()
        data = await client.list_applications(
            query=params.query,
            project=params.project_name,
            env=params.env,
            target_name=params.target_name,
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
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
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_get_application", annotations=_ro("查看应用详情"))
async def vela_get_application(params: AppNameInput) -> str:
    """查看应用详情（基础信息、策略、环境绑定、资源统计）。

    对应 API：GET /api/v1/applications/{appName}
    """
    try:
        client = await get_vela_client()
        data = await client.get_application(params.app_name)
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_kv(f"应用：{params.app_name}", data)
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_get_app_status", annotations=_ro("查看应用运行状态"))
async def vela_get_app_status(params: AppStatusInput) -> str:
    """查看应用运行状态（全部环境概览或指定环境详情）。

    对应 API：GET /api/v1/applications/{app}/status
             GET /api/v1/applications/{app}/envs/{env}/status
    """
    try:
        client = await get_vela_client()
        data = await client.get_application_status(params.app_name, env=params.env)
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        if params.env:
            status = data.get("status") or {}
            lines = [
                f"# 应用 {params.app_name} @ {params.env} 状态",
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
            return "\n".join(lines)
        rows = [
            {"envName": s.get("envName"), "status": (s.get("status") or {}).get("status")}
            for s in (data.get("status") or [])
        ]
        return render_list(
            f"应用 {params.app_name} 各环境状态",
            rows,
            [("envName", "环境"), ("status", "状态")],
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_components", annotations=_ro("查看应用组件"))
async def vela_list_components(params: ListComponentsInput) -> str:
    """查看应用的组件列表；指定 component 时返回组件详情（properties、traits、definition）。

    对应 API：GET /api/v1/applications/{app}/components[/{comp}]
    """
    try:
        client = await get_vela_client()
        if params.component:
            data = await client.get_component(params.app_name, params.component)
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_kv(f"组件：{params.app_name}/{params.component}", data)
        data = await client.list_components(params.app_name, env=params.env)
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {params.app_name} 组件列表",
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
async def vela_list_revisions(params: ListRevisionsInput) -> str:
    """查看应用版本（revision）历史，可用于回滚前确认目标版本。

    对应 API：GET /api/v1/applications/{app}/revisions
    """
    try:
        client = await get_vela_client()
        data = await client.list_revisions(
            params.app_name,
            env=params.env,
            status=params.status,
            page=params.page,
            page_size=params.page_size,
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {params.app_name} 版本历史",
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
async def vela_list_deploy_records(params: ListDeployRecordsInput) -> str:
    """查看应用在指定环境的部署记录。

    对应 API：GET /api/v1/applications/{app}/envs/{env}/records
    """
    try:
        client = await get_vela_client()
        data = await client.list_deploy_records(
            params.app_name, params.env, page=params.page, page_size=params.page_size
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"应用 {params.app_name} @ {params.env} 部署记录",
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
async def vela_list_workflow_records(params: ListWorkflowRecordsInput) -> str:
    """三合一查询：不填 workflow_name 列出工作流；填 workflow_name 列出执行记录；
    再填 record 返回该次执行详情（含各步骤状态，可据此取日志）。

    对应 API：GET /api/v1/applications/{app}/workflows[/{wf}/records[/{record}]]
    """
    try:
        client = await get_vela_client()
        if not params.workflow_name:
            data = await client.list_workflows(params.app_name)
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_list(
                f"应用 {params.app_name} 工作流列表",
                data.get("workflows") or [],
                [
                    ("name", "名称"),
                    ("envName", "环境"),
                    ("default", "默认"),
                    ("mode", "模式"),
                    ("createTime", "创建时间"),
                ],
            )
        if params.record:
            data = await client.get_workflow_record(
                params.app_name, params.workflow_name, params.record
            )
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            steps = data.get("steps") or []
            lines = [render_kv(f"执行记录：{params.record}",
                               {k: v for k, v in data.items() if k != "steps"})]
            if steps:
                lines += ["", render_list(
                    "步骤", steps,
                    [("name", "步骤"), ("type", "类型"), ("phase", "阶段"), ("message", "信息")],
                )]
            return "\n".join(lines)
        data = await client.list_workflow_records(
            params.app_name, params.workflow_name,
            page=params.page, page_size=params.page_size,
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"工作流 {params.workflow_name} 执行记录",
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
async def vela_get_workflow_logs(params: GetWorkflowLogsInput) -> str:
    """查看工作流执行记录中某个步骤的日志（step 必填，步骤名可先查记录详情获取）。

    对应 API：GET .../workflows/{wf}/records/{record}/logs?step=
    """
    try:
        client = await get_vela_client()
        data = await client.get_workflow_record_logs(
            params.app_name, params.workflow_name, params.record, params.step
        )
        log = data.get("log") or "（无日志）"
        return (
            f"# 步骤 {params.step} 日志\n\n"
            f"记录：{params.record}（来源：{data.get('source') or '-'}）\n\n"
            f"```\n{log}\n```"
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_envs", annotations=_ro("列出环境"))
async def vela_list_envs(params: ListEnvsInput) -> str:
    """列出环境（env）及其关联的交付目标。

    对应 API：GET /api/v1/envs
    """
    try:
        client = await get_vela_client()
        data = await client.list_envs(
            project=params.project_name, page=params.page, page_size=params.page_size
        )
        if params.response_format == ResponseFormat.JSON:
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
async def vela_list_targets(params: ListTargetsInput) -> str:
    """列出交付目标（target，即集群+命名空间组合）。

    对应 API：GET /api/v1/targets
    """
    try:
        client = await get_vela_client()
        data = await client.list_targets(
            project=params.project_name, page=params.page, page_size=params.page_size
        )
        if params.response_format == ResponseFormat.JSON:
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
async def vela_list_clusters(params: ListClustersInput) -> str:
    """列出纳管集群；指定 cluster_name 时返回集群详情（含资源信息）。

    对应 API：GET /api/v1/clusters[/{clusterName}]
    """
    try:
        client = await get_vela_client()
        if params.cluster_name:
            data = await client.get_cluster(params.cluster_name)
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_kv(f"集群：{params.cluster_name}", data)
        data = await client.list_clusters(
            query=params.query, page=params.page, page_size=params.page_size
        )
        if params.response_format == ResponseFormat.JSON:
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
async def vela_list_addons(params: ListAddonsInput) -> str:
    """查看插件（addon）市场列表 / 已启用插件 / 单个插件详情与状态。

    对应 API：GET /api/v1/addons[...]、GET /api/v1/enabled_addon
    """
    try:
        client = await get_vela_client()
        if params.addon_name:
            detail = await client.get_addon(params.addon_name, registry=params.registry)
            status = await client.get_addon_status(params.addon_name)
            if params.response_format == ResponseFormat.JSON:
                return to_json({"detail": detail, "status": status})
            merged = {
                "name": detail.get("name") or params.addon_name,
                "version": detail.get("version"),
                "description": detail.get("description"),
                "registryName": detail.get("registryName"),
                "availableVersions": detail.get("availableVersions"),
                "phase": status.get("phase"),
                "installedVersion": status.get("installedVersion"),
                "clusters": status.get("clusters"),
            }
            return render_kv(f"插件：{params.addon_name}", merged)
        if params.enabled_only:
            data = await client.list_enabled_addons()
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            return render_list(
                "已启用插件",
                data.get("enabledAddons") or [],
                [("name", "名称"), ("phase", "状态")],
            )
        data = await client.list_addons(registry=params.registry, query=params.query)
        if params.response_format == ResponseFormat.JSON:
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
async def vela_list_definitions(params: ListDefinitionsInput) -> str:
    """查看组件/运维特征/策略/工作流步骤定义；指定 definition_name 返回参数 schema。

    对应 API：GET /api/v1/definitions[/{name}]?type=
    """
    try:
        client = await get_vela_client()
        if params.definition_name:
            data = await client.get_definition(
                params.definition_name, params.def_type.value
            )
            if params.response_format == ResponseFormat.JSON:
                return to_json(data)
            schema = data.get("schema")
            lines = [render_kv(
                f"定义：{params.definition_name}（{params.def_type.value}）",
                {k: v for k, v in data.items() if k not in ("schema", "uiSchema")},
            )]
            if schema:
                lines += ["", "## 参数 Schema", "", f"```json\n{to_json(schema)}\n```"]
            return "\n".join(lines)
        data = await client.list_definitions(
            params.def_type.value, query_all=params.query_all
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        return render_list(
            f"{params.def_type.value} 定义列表",
            data.get("definitions") or [],
            [("name", "名称"), ("description", "描述"), ("ownerAddon", "所属插件")],
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_velaql_query", annotations=_ro("VelaQL 查询"))
async def vela_velaql_query(params: VelaQLInput) -> str:
    """执行 VelaQL 查询（Pod 列表、容器日志、资源拓扑等运行时数据）。

    对应 API：GET /api/v1/query?velaql=
    常用视图：component-pod-view、component-service-view、collect-logs、service-view
    """
    try:
        client = await get_vela_client()
        data = await client.velaql_query(params.velaql)
        return f"# VelaQL 查询结果\n\n```json\n{to_json(data)}\n```"
    except Exception as e:
        return handle_error(e)


class SystemInfoInput(BaseModel):
    """查询系统信息的输入参数"""

    model_config = _INPUT_CONFIG

    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(name="vela_system_info", annotations=_ro("查看平台系统信息"))
async def vela_system_info(params: SystemInfoInput) -> str:
    """查看 VelaUX 平台系统信息：KubeVela 版本、登录方式、集群/应用统计、已启用插件等。

    适合作为接入新环境后的连通性与版本自检。
    对应 API：GET /api/v1/system_info
    """
    try:
        client = await get_vela_client()
        data = await client.get_system_info()
        if params.response_format == ResponseFormat.JSON:
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
async def vela_compare_application(params: CompareApplicationInput) -> str:
    """对比应用配置差异（诊断配置漂移）。三种模式：

    - 仅传 env：最新配置 vs 该环境集群运行态
    - 传 revision + compare_with=running：指定版本 vs 集群运行态
    - 传 revision + compare_with=latest：指定版本 vs 最新配置

    对应 API：POST /api/v1/applications/{app}/compare
    """
    try:
        if not params.revision and not params.env:
            return "错误：env 与 revision 至少需要提供一个"
        client = await get_vela_client()
        data = await client.compare_application(
            params.app_name,
            env=params.env,
            revision=params.revision,
            compare_with=params.compare_with,
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        is_diff = data.get("isDiff")
        lines = [
            f"# 应用对比：{params.app_name}",
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
async def vela_get_application_manifest(params: GetManifestInput) -> str:
    """导出应用的 Application CR YAML 清单（GitOps 迁移 / 备份 / 审计用）。

    source=latest 导出最新渲染配置；source=running 导出集群中实际运行的 CR。
    基于 compare 接口的 YAML 字段实现。
    对应 API：POST /api/v1/applications/{app}/compare
    """
    try:
        client = await get_vela_client()
        data = await client.compare_application(params.app_name, env=params.env)
        # compareLatestWithRunning：base=集群运行态，target=最新渲染配置
        yaml_text = (
            data.get("targetAppYAML")
            if params.source == "latest"
            else data.get("baseAppYAML")
        ) or ""
        yaml_text = yaml_text.strip()
        if not yaml_text:
            hint = (
                "集群运行态为空（应用可能尚未部署到该环境）"
                if params.source == "running"
                else "最新配置渲染为空，请检查应用与环境绑定"
            )
            return f"错误：未获取到 YAML 清单，{hint}"
        return (
            f"# 应用清单：{params.app_name}（env={params.env}，source={params.source}）\n\n"
            f"```yaml\n{yaml_text}\n```"
        )
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_triggers", annotations=_ro("查看应用触发器"))
async def vela_list_triggers(params: ListTriggersInput) -> str:
    """列出应用的 webhook 触发器（含 token，可拼接触发地址）。

    对应 API：GET /api/v1/applications/{app}/triggers
    """
    try:
        client = await get_vela_client()
        data = await client.list_triggers(params.app_name)
        triggers = data.get("triggers") or []
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
        table = render_list(
            f"应用触发器：{params.app_name}",
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


@mcp.tool(name="vela_list_projects", annotations=_ro("列出项目"))
async def vela_list_projects(params: ListProjectsInput) -> str:
    """列出平台中的项目（project），创建应用前用于确认可选项目。

    对应 API：GET /api/v1/projects
    """
    try:
        client = await get_vela_client()
        data = await client.list_projects(
            page=params.page, page_size=params.page_size
        )
        if params.response_format == ResponseFormat.JSON:
            return to_json(data)
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
        return render_list(
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
    except Exception as e:
        return handle_error(e)


@mcp.tool(name="vela_list_project_targets", annotations=_ro("列出项目交付目标"))
async def vela_list_project_targets(params: ListProjectTargetsInput) -> str:
    """列出指定项目可用的交付目标（target），部署前确认目标合法性。

    对应 API：GET /api/v1/projects/{projectName}/targets
    """
    try:
        client = await get_vela_client()
        data = await client.list_project_targets(params.project_name)
        if params.response_format == ResponseFormat.JSON:
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
            f"项目 {params.project_name} 的交付目标",
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
async def vela_list_project_users(params: ListProjectUsersInput) -> str:
    """列出指定项目的成员及其角色，用于排查权限（403）类问题。

    对应 API：GET /api/v1/projects/{projectName}/users
    """
    try:
        client = await get_vela_client()
        data = await client.list_project_users(params.project_name)
        if params.response_format == ResponseFormat.JSON:
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
            f"项目 {params.project_name} 的成员",
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


# ==================== 写工具（只读模式下不注册） ====================
_read_only = os.getenv("VELA_READ_ONLY", "false").lower() == "true"

if not _read_only:

    @mcp.tool(name="vela_create_application", annotations=_rw("创建 KubeVela 应用"))
    async def vela_create_application(params: CreateApplicationInput) -> str:
        """创建应用（含首个组件），可同时绑定环境。

        对应 API：POST /api/v1/applications
        """
        try:
            client = await get_vela_client()
            component: dict[str, Any] = {
                "name": params.component_name,
                "componentType": params.component_type,
            }
            if params.properties:
                component["properties"] = params.properties
            data = await client.create_application(
                name=params.name,
                project=params.project_name,
                component=component,
                alias=params.alias,
                description=params.description,
                env_binding=params.env_binding,
            )
            return render_kv("应用创建成功", data) + (
                "\n\n> 提示：应用尚未部署，可调用 vela_deploy_application 触发部署。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_deploy_application", annotations=_rw("部署应用"))
    async def vela_deploy_application(params: DeployApplicationInput) -> str:
        """触发应用部署工作流（异步）。返回执行记录名，可用 vela_list_workflow_records 跟踪进度。

        对应 API：POST /api/v1/applications/{app}/deploy
        """
        try:
            client = await get_vela_client()
            data = await client.deploy_application(
                params.app_name,
                workflow_name=params.workflow_name,
                note=params.note,
                force=params.force,
            )
            record = data.get("record") or {}
            lines = [
                "# 部署已触发",
                "",
                "| 属性 | 值 |",
                "|------|-----|",
                f"| 应用 | {params.app_name} |",
                f"| 版本 | {data.get('version')} |",
                f"| 工作流 | {record.get('workflowName') or params.workflow_name or '-'} |",
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
    async def vela_dry_run_application(params: DryRunApplicationInput) -> str:
        """部署预演：渲染出将要下发的 K8s 资源 YAML，但不实际部署（安全）。

        对应 API：POST /api/v1/applications/{app}/dry-run
        """
        try:
            client = await get_vela_client()
            data = await client.dry_run_application(
                params.app_name,
                env=params.env,
                workflow=params.workflow,
                version=params.version,
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
    async def vela_rollback_application(params: RollbackApplicationInput) -> str:
        """回滚应用到指定历史版本（危险操作，需 confirm=true）。

        对应 API：POST /api/v1/applications/{app}/revisions/{revision}/rollback
        """
        if not params.confirm:
            return _CONFIRM_HINT
        try:
            client = await get_vela_client()
            data = await client.rollback_application(params.app_name, params.revision)
            record = data.get("record") or {}
            return (
                f"# 回滚已触发\n\n应用 {params.app_name} → 版本 {params.revision}\n\n"
                f"执行记录：{record.get('name')}（工作流 {record.get('workflowName')}）\n\n"
                "> 提示：可调用 vela_list_workflow_records 跟踪回滚进度。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_resume_workflow", annotations=_rw("恢复挂起的工作流"))
    async def vela_resume_workflow(params: ResumeWorkflowInput) -> str:
        """恢复挂起（suspend）的工作流，常用于人工审批后继续发布。

        对应 API：GET .../records/{record}/resume
        """
        try:
            client = await get_vela_client()
            await client.resume_workflow_record(
                params.app_name, params.workflow_name, params.record, step=params.step
            )
            return (
                f"工作流已恢复：{params.app_name}/{params.workflow_name}/{params.record}\n\n"
                "> 提示：可调用 vela_list_workflow_records 跟踪后续进度。"
            )
        except Exception as e:
            return handle_error(e)

    @mcp.tool(
        name="vela_terminate_workflow",
        annotations=_rw("终止执行中的工作流", destructive=True),
    )
    async def vela_terminate_workflow(params: TerminateWorkflowInput) -> str:
        """终止执行中的工作流（危险操作，需 confirm=true）。

        对应 API：GET .../records/{record}/terminate
        """
        if not params.confirm:
            return _CONFIRM_HINT
        try:
            client = await get_vela_client()
            await client.terminate_workflow_record(
                params.app_name, params.workflow_name, params.record
            )
            return f"工作流已终止：{params.app_name}/{params.workflow_name}/{params.record}"
        except Exception as e:
            return handle_error(e)

    @mcp.tool(name="vela_create_trigger", annotations=_rw("创建应用触发器"))
    async def vela_create_trigger(params: CreateTriggerInput) -> str:
        """创建应用的 webhook 触发器，payload_type 支持
        custom / dockerhub / acr / harbor / jfrog，返回 token。

        对应 API：POST /api/v1/applications/{app}/triggers
        """
        try:
            client = await get_vela_client()
            data = await client.create_trigger(
                params.app_name,
                name=params.name,
                workflow_name=params.workflow_name,
                payload_type=params.payload_type,
                description=params.description,
                component_name=params.component_name,
                registry=params.registry,
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

    mcp.settings.host = host
    mcp.settings.port = port

    if transport == "sse":
        app: Any = mcp.sse_app()
        endpoint = "/sse"
    else:
        app = mcp.streamable_http_app()
        endpoint = "/mcp"

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
