"""工单工具层：模拟 Agent 对接 OA/工单系统时的工具封装。

真实项目中，这里的 query_yesterday_tickets 可能会调用 OA HTTP API；
本 Demo 直接查询 SQLite，但保留了鉴权、超时、重试和审计的思路。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable
from uuid import uuid4

from . import db
from .ticket_db import query_tickets, summarize_tickets, today_range

logger = logging.getLogger(__name__)


def _audit(
    request_id: str,
    tool_name: str,
    input_data: dict[str, Any],
    output_data: Any,
    status: str,
    elapsed_ms: int,
    error: str | None = None,
) -> None:
    """把工具执行结果写入审计表；生产环境应进一步脱敏。"""
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


def with_retry(
    operation: Callable[[], Any],
    retries: int = 2,
    timeout_seconds: float = 5,
) -> Any:
    """一个教学版重试包装器。

    注意：这里的 timeout_seconds 用于演示参数边界；同步函数真正的超时
    通常应由 HTTP 客户端设置 connect/read/total timeout，而不是靠 sleep 判断。
    """
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            result = operation()
            if time.monotonic() - started > timeout_seconds:
                raise TimeoutError(f"工具执行超过 {timeout_seconds} 秒")
            return result
        except (TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt < retries:
                # 指数退避：1 秒、2 秒……真实生产环境还应加随机抖动。
                time.sleep(2**attempt)
    raise last_error or RuntimeError("工具执行失败")


def query_yesterday_tickets(date_text: str | None = None) -> dict[str, Any]:
    """工具：查询指定日期工单并返回结构化统计。

    返回值刻意保持稳定，方便未来把 SQLite 替换成 OA HTTP API，
    Agent 上层不需要跟着改。
    """
    request_id = f"tool-{uuid4().hex[:10]}"
    tool_name = "query_yesterday_tickets"
    day, start, end = today_range(date_text)
    input_data = {"date": day, "start": start, "end": end}
    started = time.monotonic()
    try:
        tickets = with_retry(lambda: query_tickets(start, end))
        output = {"date": day, "tickets": tickets, "summary": summarize_tickets(tickets)}
        _audit(request_id, tool_name, input_data, output, "success", int((time.monotonic() - started) * 1000))
        logger.info("tool_success request_id=%s tool=%s count=%s", request_id, tool_name, len(tickets))
        return {"request_id": request_id, **output}
    except Exception as exc:
        _audit(request_id, tool_name, input_data, None, "failed", int((time.monotonic() - started) * 1000), str(exc))
        logger.exception("tool_failed request_id=%s tool=%s", request_id, tool_name)
        raise
