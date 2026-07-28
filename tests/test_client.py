"""VelaUX 客户端测试：JWT 登录、401 自动续期重放、bcode 错误解析"""

import pytest
import respx
from httpx import Response

from mcp_kubevela.clients.base import VelaApiError, VelaAuthError
from mcp_kubevela.clients.velaux import VelaUXClient

BASE = "http://vela.test:8000/api/v1"


def make_client(monkeypatch) -> VelaUXClient:
    monkeypatch.setenv("VELA_URL", "http://vela.test:8000")
    monkeypatch.setenv("VELA_USERNAME", "admin")
    monkeypatch.setenv("VELA_PASSWORD", "pass123")
    return VelaUXClient()


@respx.mock
async def test_login_and_list_applications(monkeypatch):
    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    route = respx.get(f"{BASE}/applications").mock(
        return_value=Response(200, json={"applications": [{"name": "demo"}]})
    )
    client = make_client(monkeypatch)
    data = await client.list_applications(project="default")
    assert data["applications"][0]["name"] == "demo"
    assert route.calls.last.request.headers["Authorization"] == "Bearer at1"
    assert "project=default" in str(route.calls.last.request.url)


@respx.mock
async def test_401_refresh_and_replay(monkeypatch):
    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "old", "refreshToken": "rt1", "user": {}}
        )
    )
    respx.get(f"{BASE}/auth/refresh_token").mock(
        return_value=Response(200, json={"accessToken": "new", "refreshToken": "rt2"})
    )
    route = respx.get(f"{BASE}/envs").mock(
        side_effect=[
            Response(401, json={"BusinessCode": 12002, "Message": "token expired"}),
            Response(200, json={"envs": [], "total": 0}),
        ]
    )
    client = make_client(monkeypatch)
    data = await client.list_envs()
    assert data["total"] == 0
    assert route.call_count == 2
    assert route.calls.last.request.headers["Authorization"] == "Bearer new"


@respx.mock
async def test_bcode_error(monkeypatch):
    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    respx.get(f"{BASE}/applications/miss").mock(
        return_value=Response(
            404, json={"BusinessCode": 10001, "Message": "application not exist"}
        )
    )
    client = make_client(monkeypatch)
    with pytest.raises(VelaApiError) as ei:
        await client.get_application("miss")
    assert ei.value.status_code == 404
    assert ei.value.business_code == 10001


async def test_missing_credentials(monkeypatch):
    monkeypatch.setenv("VELA_URL", "http://vela.test:8000")
    monkeypatch.delenv("VELA_USERNAME", raising=False)
    monkeypatch.delenv("VELA_PASSWORD", raising=False)
    client = VelaUXClient()
    with pytest.raises(VelaAuthError):
        await client.list_envs()


@respx.mock
async def test_resume_uses_get(monkeypatch):
    """VelaUX 的 resume/terminate 是 GET 方法，防止回归"""
    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    route = respx.get(
        f"{BASE}/applications/app1/workflows/wf1/records/r1/resume"
    ).mock(return_value=Response(200, json={}))
    client = make_client(monkeypatch)
    await client.resume_workflow_record("app1", "wf1", "r1")
    assert route.called


@respx.mock
async def test_compare_body_modes(monkeypatch):
    """compare 三种模式的请求体构造（对应 AppCompareReq oneof）"""
    import json

    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    route = respx.post(f"{BASE}/applications/app1/compare").mock(
        return_value=Response(200, json={"isDiff": False})
    )
    client = make_client(monkeypatch)

    await client.compare_application("app1", env="prod")
    body = json.loads(route.calls.last.request.content)
    assert body == {"compareLatestWithRunning": {"env": "prod"}}

    await client.compare_application("app1", revision="v3", compare_with="running")
    body = json.loads(route.calls.last.request.content)
    assert body == {"compareRevisionWithRunning": {"revision": "v3"}}

    await client.compare_application("app1", revision="v3", compare_with="latest")
    body = json.loads(route.calls.last.request.content)
    assert body == {"compareRevisionWithLatest": {"revision": "v3"}}


@respx.mock
async def test_create_trigger_body(monkeypatch):
    """触发器创建请求体：type 固定 webhook"""
    import json

    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    route = respx.post(f"{BASE}/applications/app1/triggers").mock(
        return_value=Response(200, json={"name": "t1", "token": "tok123"})
    )
    client = make_client(monkeypatch)
    data = await client.create_trigger(
        "app1", name="t1", workflow_name="wf-prod", payload_type="harbor",
        component_name="web",
    )
    body = json.loads(route.calls.last.request.content)
    assert body["type"] == "webhook"
    assert body["payloadType"] == "harbor"
    assert body["componentName"] == "web"
    assert data["token"] == "tok123"


@respx.mock
async def test_list_projects_and_project_scoped(monkeypatch):
    """projects 列表分页参数与项目级 targets/users 路径"""
    respx.post(f"{BASE}/auth/login").mock(
        return_value=Response(
            200, json={"accessToken": "at1", "refreshToken": "rt1", "user": {}}
        )
    )
    route_p = respx.get(f"{BASE}/projects").mock(
        return_value=Response(
            200,
            json={"projects": [{"name": "default", "namespace": "default"}], "total": 1},
        )
    )
    route_t = respx.get(f"{BASE}/projects/default/targets").mock(
        return_value=Response(200, json={"targets": [], "total": 0})
    )
    route_u = respx.get(f"{BASE}/projects/default/users").mock(
        return_value=Response(
            200,
            json={"users": [{"name": "admin", "userRoles": ["project-admin"]}], "total": 1},
        )
    )
    client = make_client(monkeypatch)
    data = await client.list_projects(page=0, page_size=10)
    assert data["total"] == 1
    assert "pageSize=10" in str(route_p.calls.last.request.url)
    await client.list_project_targets("default")
    assert route_t.called
    users = await client.list_project_users("default")
    assert users["users"][0]["userRoles"] == ["project-admin"]
    assert route_u.called
