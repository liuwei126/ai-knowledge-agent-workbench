"""通用工具审计模块：各 Agent 工具层共用的调用留痕。

设计动机：审计逻辑（写 tool_audit_logs）本来长在 ticket_tools.py 里，
线索分级模块接入后抽成公共模块，避免每个工具层复制一份。
历史模块（工单）暂未迁移，保持既有代码不动；新工具一律走本模块。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import db


def audit_tool_call(
    request_id: str,
    tool_name: str,
    input_data: dict[str, Any],
    output_data: Any,
    status: str,
    elapsed_ms: int,
    error: str | None = None,
) -> None:
    """把一次工具调用写入 tool_audit_logs；生产环境应进一步脱敏。"""
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO tool_audit_logs
            (request_id, tool_name, input_json, output_json, status, elapsed_ms, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                tool_name,
                json.dumps(input_data, ensure_ascii=False),
                json.dumps(output_data, ensure_ascii=False) if output_data is not None else None,
                status,
                elapsed_ms,
                error,
                db._now(),
            ),
        )


def list_audit_logs(tool_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """按工具名过滤查询最近的审计记录，便于排查与演示。"""
    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if tool_name:
            rows = conn.execute(
                "SELECT * FROM tool_audit_logs WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
                (tool_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tool_audit_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(row) for row in rows]
