"""server 基础测试：工具注册、只读模式、错误处理"""

import httpx
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
