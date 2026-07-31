# src/mcp_kubevela/velaql/compiler.py
"""Pure compiler: (VelaQLView, params dict) -> velaql string.

The compiler is the only place that knows how to translate typed params
into the wire-format string. It:

  * Validates the params against the view's ParamSchema (Pydantic v2).
  * Translates pydantic.ValidationError into VelaQLParamError with
    structured `missing` and `bad` fields so the tool can render a
    human-readable (and LLM-parseable) error message.
  * Skips None values — optional params that the caller did not provide
    are absent from the output (per spec §11).
  * Emits key=value pairs in Pydantic field declaration order
    (model_fields iteration).
  * Appends the canonical suffix (.status for most views; collect-logs has none).
"""

from typing import Any

from pydantic import ValidationError

from mcp_kubevela.velaql.errors import VelaQLParamError
from mcp_kubevela.velaql.views import VIEWS, VelaQLView

# Canonical suffix per view. Verified against a live VelaUX (vela.lbxdrugs.com):
#   * Most views render with a `.status` suffix (or no suffix at all — both work).
#   * `collect-logs` MUST have NO suffix: the legacy `.logs` suffix returns
#     HTTP 502 Bad Gateway from the upstream, so we emit the bare form that
#     matches the real, working query URLs.
_SUFFIX: dict[VelaQLView, str] = {
    VelaQLView.COLLECT_LOGS: "",
}


def _format_value(v: Any) -> str:
    """Render a Pydantic-validated value into its wire string form."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def compile(view: VelaQLView, params: dict[str, Any]) -> str:
    """Validate `params` against `view`'s ParamSchema, then format velaql.

    Raises:
        VelaQLParamError: with `missing` and `bad` populated from
            the underlying Pydantic ValidationError.
    """
    spec = VIEWS[view]
    try:
        validated = spec.param_schema.model_validate(params)
    except ValidationError as exc:
        missing: list[str] = []
        bad: dict[str, str] = {}
        for err in exc.errors():
            loc = err.get("loc", ())
            name = str(loc[0]) if loc else "<root>"
            if err.get("type") == "missing":
                if name not in missing:
                    missing.append(name)
            else:
                # First sentence of the message — enough for the LLM to act.
                msg = err.get("msg", "invalid value")
                bad[name] = f"{msg} (got {err.get('input')!r})"
        raise VelaQLParamError(
            f"params for view '{view.value}' failed validation",
            missing=missing,
            bad=bad,
        ) from exc

    # Build the {k=v,k=v} body in Pydantic field declaration order.
    # Only include fields that were explicitly provided by the caller
    # (check model_fields_set) AND have non-None values.
    parts: list[str] = []
    for field_name in type(validated).model_fields:
        # Skip fields that weren't explicitly provided by caller
        # (even if they have default values)
        if field_name not in validated.model_fields_set:
            continue
        value = getattr(validated, field_name)
        if value is None:
            continue
        parts.append(f"{field_name}={_format_value(value)}")

    suffix = _SUFFIX.get(view, ".status")
    return f"{view.value}{{{','.join(parts)}}}{suffix}"
