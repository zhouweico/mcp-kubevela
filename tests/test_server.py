"""server 基础测试：工具注册、只读模式、错误处理"""

import httpx2 as httpx
import pytest

from mcp_kubevela import server
from mcp_kubevela.clients.base import VelaApiError, VelaAuthError
from mcp_kubevela.velaql import VelaQLView

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
        # vela_velaql_query 的 params 是一个真正的扁平参数（dict[str, Any]），
        # 不是包装对象，因此允许它包含 params 属性名。
        if tool.name != "vela_velaql_query":
            assert "params" not in props, f"{tool.name} 仍使用 params 包装对象"
        assert "ctx" not in props, f"{tool.name} 暴露了 ctx 参数"


async def test_flat_params_required_fields():
    """抽样校验扁平参数的 required 字段与约束迁移正确。"""
    tools = {t.name: t for t in await server.mcp.list_tools()}

    # 全部必填的工具
    assert set(tools["vela_get_workflow_logs"].input_schema["required"]) == {
        "app_name", "workflow_name", "record", "step"
    }
    # vela_velaql_query 的必填参数为 view 与 params（cluster / response_format 有默认值）
    assert set(tools["vela_velaql_query"].input_schema["required"]) == {"view", "params"}
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


"""vela_velaql_query tool — end-to-end tool tests"""


async def test_velaql_query_tool_registered():
    tools = {t.name for t in await server.mcp.list_tools()}
    assert "vela_velaql_query" in tools


def test_velaql_query_input_schema_requires_view_and_params():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    tool = next(t for t in tools if t.name == "vela_velaql_query")
    schema = tool.input_schema
    assert set(schema["required"]) == {"view", "params"}
    # view is an enum — FastMCP renders it as a $ref into $defs
    view_ref = schema["properties"]["view"]
    view_schema = schema["$defs"][view_ref["$ref"].split("/")[-1]]
    assert "enum" in view_schema
    assert len(view_schema["enum"]) == 9
    assert "component-pod-view" in view_schema["enum"]
    assert "collect-logs" in view_schema["enum"]
    assert "application-resource-detail-view" in view_schema["enum"]
    # params is a free-form object
    assert schema["properties"]["params"]["type"] == "object"


"""vela_velaql_query end-to-end — exercise full tool body via in-memory mocks.

NOTE: calling the tool function directly (not through the MCP protocol) skips
FastMCP's enum coercion, so valid views are passed as VelaQLView members.
"""


class _FakeClient:
    """Stand-in for the VelaUX client used by the tool's happy path."""

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
    result = await server.vela_velaql_query(
        view=VelaQLView.COMPONENT_POD_VIEW,
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
    result = await server.vela_velaql_query(
        view=VelaQLView.COMPONENT_POD_VIEW,
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
        await server.vela_velaql_query(
            view="totally-bogus-view",  # type: ignore[arg-type]
            params={"appNs": "x", "appName": "y"},
        )
    assert client.calls == []


async def test_velaql_query_collect_logs_with_optional_defaults(fake_client):
    client = fake_client({"status": {"logs": "line1\nline2"}})
    await server.vela_velaql_query(
        view=VelaQLView.COLLECT_LOGS,
        params={
            "cluster": "devops-test",
            "namespace": "devops-admin",
            "pod": "xdevops-ui-5ffb6c4b-wxb54",
            "container": "xdevops-ui",
        },
    )
    # Optional params (previous/timestamps/tailLines) MUST be absent from wire string.
    # collect-logs has NO suffix — the legacy `.logs` suffix returns HTTP 502 on
    # real VelaUX, so the wire string must end with `}`.
    wire = client.calls[0]
    assert "previous" not in wire
    assert "timestamps" not in wire
    assert "tailLines" not in wire
    assert wire.endswith("}")
