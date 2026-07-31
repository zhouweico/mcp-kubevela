"""velaql/compiler.py — pure (view, params) -> velaql string tests"""

import pytest

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
    # collect-logs has NO suffix — the legacy `.logs` suffix returns HTTP 502 on
    # real VelaUX (verified against vela.lbxdrugs.com), so we emit the bare form.
    assert out == (
        "collect-logs{cluster=devops-test,namespace=devops-admin,"
        "pod=xdevops-ui-5ffb6c4b-wxb54,container=xdevops-ui}"
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
    # No `.logs` suffix (see test_compile_collect_logs_with_defaults_omitted).
    assert out == (
        "collect-logs{cluster=c,namespace=n,pod=p,container=ctr,"
        "previous=true,timestamps=false,tailLines=500}"
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
