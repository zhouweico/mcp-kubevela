# src/mcp_kubevela/velaql/__init__.py
"""velaql: schema-typed VelaQL view catalog and compiler.

Public API:
    VelaQLView  — enum of all 9 supported views
    VIEWS       — view -> ViewSpec registry
    compile     — pure function: (view, params) -> velaql string
    VelaQLError, VelaQLParamError — typed exceptions
"""

from mcp_kubevela.velaql.compiler import compile
from mcp_kubevela.velaql.errors import VelaQLError, VelaQLParamError
from mcp_kubevela.velaql.views import VIEWS, VelaQLView

__all__ = [
    "VelaQLView",
    "VIEWS",
    "compile",
    "VelaQLError",
    "VelaQLParamError",
]
