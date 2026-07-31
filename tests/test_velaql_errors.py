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
