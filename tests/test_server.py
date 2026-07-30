"""server 基础测试：工具注册、只读模式、错误处理"""

import httpx2 as httpx
import pytest

from mcp_kubevela import server
from mcp_kubevela.clients.base import VelaApiError, VelaAuthError

EXPECTED_READONLY = {
    "vela_list_applications",
    "vela_get_application",
    "vela_get_app_status",
    "vela_list_components",
    "vela_list_revisions",
    "vela_list_deploy_records",
    "vela_list_workflow_records",
    "vela_get_workflow_logs",
    "vela_list_envs",
    "vela_list_targets",
    "vela_list_clusters",
    "vela_list_addons",
    "vela_list_definitions",
    "vela_velaql_query",
    "vela_system_info",
    "vela_compare_application",
    "vela_get_application_manifest",
    "vela_list_triggers",
    "vela_list_projects",
    "vela_list_project_targets",
    "vela_list_project_users",
}

EXPECTED_WRITE = {
    "vela_create_application",
    "vela_deploy_application",
    "vela_dry_run_application",
    "vela_rollback_application",
    "vela_resume_workflow",
    "vela_terminate_workflow",
    "vela_create_trigger",
}


async def test_all_tools_registered():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert EXPECTED_READONLY <= tools
    # 默认非只读模式下写工具也应注册
    assert EXPECTED_WRITE <= tools
    assert len(tools) == len(EXPECTED_READONLY) + len(EXPECTED_WRITE)


async def test_readonly_annotations():
    for tool in await server.mcp.list_tools():
        annotations = tool.annotations
        assert annotations is not None, tool.name
        if tool.name in EXPECTED_READONLY:
            assert annotations.read_only_hint is True, tool.name
        else:
            assert annotations.read_only_hint is False, tool.name


async def test_flat_annotated_params():
    """所有工具均使用扁平 Annotated 入参模式，不再有 params 包装对象，
    也不应暴露 ctx 参数（MCP SDK 自动跳过 Context 类型）。"""
    tools = await server.mcp.list_tools()
    for tool in tools:
        props = set(tool.input_schema.get("properties", {}).keys())
        assert "params" not in props, f"{tool.name} 仍使用 params 包装对象"
        assert "ctx" not in props, f"{tool.name} 暴露了 ctx 参数"


async def test_flat_params_required_fields():
    """抽样校验扁平参数的 required 字段与约束迁移正确。"""
    tools = {t.name: t for t in await server.mcp.list_tools()}

    # 全部必填的工具
    assert set(tools["vela_get_workflow_logs"].input_schema["required"]) == {
        "app_name", "workflow_name", "record", "step"
    }
    # 无必填（全部带默认值）的工具
    assert tools["vela_list_applications"].input_schema.get("required", []) == []
    assert tools["vela_system_info"].input_schema.get("required", []) == []
    # ctx 参数不应出现在 schema 中
    assert "ctx" not in tools["vela_create_application"].input_schema.get("properties", {})


async def test_flat_params_field_constraints():
    """抽样校验 Field 约束（min_length / ge / le / pattern）已迁移到扁平参数。"""
    tools = {t.name: t for t in await server.mcp.list_tools()}

    # app_name 的 min_length=1 约束应保留
    app_name_schema = tools["vela_get_application"].input_schema["properties"]["app_name"]
    assert app_name_schema.get("minLength") == 1
    assert app_name_schema.get("maxLength") == 64

    # page 的 ge=0 / page_size 的 ge=1, le=100 约束应保留
    page_schema = tools["vela_list_revisions"].input_schema["properties"]["page"]
    assert page_schema.get("minimum") == 0
    page_size_schema = tools["vela_list_revisions"].input_schema["properties"]["page_size"]
    assert page_size_schema.get("minimum") == 1
    assert page_size_schema.get("maximum") == 100

    # compare_with 的 pattern 约束应保留
    props = tools["vela_compare_application"].input_schema["properties"]
    compare_with_schema = props["compare_with"]
    assert compare_with_schema.get("pattern") == "^(running|latest)$"

    # payload_type 的 pattern 约束应保留
    payload_type_schema = tools["vela_create_trigger"].input_schema["properties"]["payload_type"]
    assert payload_type_schema.get("pattern") == "^(custom|dockerhub|acr|harbor|jfrog)$"


def test_handle_error_auth():
    msg = server.handle_error(VelaAuthError("缺少登录凭证"))
    assert "缺少登录凭证" in msg


def test_handle_error_api_403():
    msg = server.handle_error(VelaApiError(403, 10013, "no permission"))
    assert "权限不足" in msg


def test_handle_error_connect():
    msg = server.handle_error(httpx.ConnectError("boom"))
    assert "VELA_URL" in msg


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "stdio"),
        ("stdio", "stdio"),
        ("SSE", "sse"),
        ("http", "streamable-http"),
        ("streamable_http", "streamable-http"),
    ],
)
def test_normalize_transport(raw, expected):
    assert server._normalize_transport(raw) == expected


def test_normalize_transport_invalid():
    with pytest.raises(ValueError):
        server._normalize_transport("grpc")
