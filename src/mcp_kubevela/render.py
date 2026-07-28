"""JSON → Markdown 渲染辅助"""

from __future__ import annotations

import json
from typing import Any

_STATUS_MARKS = {
    "running": "🔄",
    "executing": "🔄",
    "succeeded": "✅",
    "success": "✅",
    "complete": "✅",
    "failed": "❌",
    "terminated": "⏹️",
    "suspending": "⏸️",
    "suspended": "⏸️",
    "workflowSuspending": "⏸️",
    "pending": "⏳",
    "initializing": "⏳",
    "enabled": "✅",
    "disabled": "⚪",
    "enabling": "🔄",
}


def fmt_status(value: Any) -> str:
    """状态值加符号标注"""
    s = str(value or "")
    mark = _STATUS_MARKS.get(s) or _STATUS_MARKS.get(s.lower())
    return f"{mark} {s}" if mark else s


def to_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        text = json.dumps(v, ensure_ascii=False, default=str)
        return text if len(text) <= 120 else text[:117] + "..."
    return str(v).replace("\n", " ")


def render_kv(title: str, data: dict[str, Any]) -> str:
    """单条对象 → Markdown 属性表（跳过 _ 前缀字段）"""
    lines = [f"# {title}", "", "| 属性 | 值 |", "|------|-----|"]
    for k, v in data.items():
        if k.startswith("_"):
            continue
        if k.lower().endswith(("status", "phase")) and isinstance(v, str):
            v = fmt_status(v)
        lines.append(f"| {k} | {_cell(v)} |")
    return "\n".join(lines)


def render_list(
    title: str,
    rows: list[Any],
    columns: list[tuple[str, str]],
    total: Any = None,
) -> str:
    """对象列表 → Markdown 表格。columns: [(field, header), ...]"""
    count_line = f"共 {total} 条" if total is not None else f"共 {len(rows)} 条"
    headers = [h for _, h in columns]
    lines = [
        f"# {title}",
        "",
        count_line,
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["------"] * len(headers)) + "|",
    ]
    for r in rows or []:
        cells = []
        for field, _ in columns:
            v = r.get(field, "") if isinstance(r, dict) else ""
            if field in ("status", "phase") and isinstance(v, str):
                v = fmt_status(v)
            cells.append(_cell(v))
        lines.append("| " + " | ".join(cells) + " |")
    if not rows:
        lines.append("（无数据）")
    return "\n".join(lines)
