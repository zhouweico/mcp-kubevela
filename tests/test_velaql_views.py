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
