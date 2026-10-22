"""工单数据访问层：把 SQL 细节集中起来，模拟一个 OA/服务台数据库。

学习重点：
1. 所有 SQL 都使用参数绑定，避免 SQL 注入；
2. 时间查询使用 [start, end) 左闭右开区间，便于走索引；
3. Agent 不直接拼 SQL，而是调用本文件暴露的业务函数。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
from typing import Any

from . import db


# ---------- 初始化样例数据 ----------

SAMPLE_TICKETS = [
    {
        "id": "T-1001",
        "title": "报销系统无法上传发票",
        "description": "财务部用户反馈上传 PDF 发票时提示文件格式错误。",
        "department": "财务部",
        "priority": "high",
        "status": "resolved",
        "assignee": "张工",
        "resolution": "修复文件扩展名校验，并增加 PDF 大小提示。",
        "created_at": "2026-10-15T09:10:00+08:00",
        "closed_at": "2026-10-15T10:30:00+08:00",
    },
    {
        "id": "T-1002",
        "title": "OA 登录缓慢",
        "description": "多个用户反馈上午登录 OA 需要等待 30 秒以上。",
        "department": "人力资源部",
        "priority": "high",
        "status": "processing",
        "assignee": "李工",
        "resolution": None,
        "created_at": "2026-10-15T10:20:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T-1003",
        "title": "新员工申请系统账号",
        "description": "请为新员工王某开通邮箱、OA 和知识库账号。",
        "department": "人力资源部",
        "priority": "normal",
        "status": "resolved",
        "assignee": "服务台",
        "resolution": "已按照入职清单开通三个系统账号。",
        "created_at": "2026-10-15T11:00:00+08:00",
        "closed_at": "2026-10-15T13:00:00+08:00",
    },
    {
        "id": "T-1004",
        "title": "数据报表导出失败",
        "description": "经营分析报表导出 Excel 时超时，暂未定位根因。",
        "department": "运营部",
        "priority": "normal",
        "status": "pending",
        "assignee": "王工",
        "resolution": None,
        "created_at": "2026-10-15T14:15:00+08:00",
        "closed_at": None,
    },
    {
        "id": "T-1005",
        "title": "移动端审批页面显示异常",
        "description": "部分 Android 手机打开审批详情时按钮错位。",
        "department": "行政部",
        "priority": "low",
        "status": "resolved",
        "assignee": "赵工",
        "resolution": "修复移动端 CSS 样式并完成回归测试。",
        "created_at": "2026-10-15T16:40:00+08:00",
        "closed_at": "2026-10-15T18:10:00+08:00",
    },
    {
        "id": "T-1006",
        "title": "VPN 无法连接",
        "description": "出差员工反馈 VPN 拨号后无法访问内部系统。",
        "department": "销售部",
        "priority": "high",
        "status": "resolved",
        "assignee": "服务台",
        "resolution": "重新下发客户端配置并指导用户更换网络。",
        "created_at": "2026-10-14T15:00:00+08:00",
        "closed_at": "2026-10-15T09:00:00+08:00",
    },
]


def seed_tickets() -> None:
    """首次启动写入样例工单；真实项目中这里通常是同步任务而非 seed。"""
    with db.get_conn() as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
        if count > 0:
            return
        for ticket in SAMPLE_TICKETS:
            conn.execute(
                """
                INSERT INTO tickets
                (id, title, description, department, priority, status,
                 assignee, resolution, created_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(ticket[field] for field in (
                    "id", "title", "description", "department", "priority",
                    "status", "assignee", "resolution", "created_at", "closed_at",
                )),
            )


def query_tickets(start: str, end: str) -> list[dict[str, Any]]:
    """查询时间范围内的工单。

    start/end 使用 ISO 时间字符串；条件是 >= start 且 < end，
    不用 DATE(created_at)，这样更容易使用时间字段索引。
    """
    with db.get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, description, department, priority, status,
                   assignee, resolution, created_at, closed_at
            FROM tickets
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (start, end),
        ).fetchall()
    return [dict(row) for row in rows]


def summarize_tickets(tickets: list[dict[str, Any]]) -> dict[str, Any]:
    """将明细转换为模型更容易理解的结构化统计结果。"""
    by_status: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_department: dict[str, int] = {}
    for ticket in tickets:
        by_status[ticket["status"]] = by_status.get(ticket["status"], 0) + 1
        by_priority[ticket["priority"]] = by_priority.get(ticket["priority"], 0) + 1
        by_department[ticket["department"]] = by_department.get(ticket["department"], 0) + 1
    return {
        "total": len(tickets),
        "by_status": by_status,
        "by_priority": by_priority,
        "by_department": by_department,
        "unresolved": [t for t in tickets if t["status"] != "resolved"],
        "high_priority": [t for t in tickets if t["priority"] == "high"],
    }


def today_range(date_text: str | None = None) -> tuple[str, str, str]:
    """返回某天的 [start, end) 范围；未传日期时默认使用昨日。"""
    if date_text:
        day = datetime.strptime(date_text, "%Y-%m-%d").date()
    else:
        # 接口语义是“昨日”，所以不传日期时默认取本地日期的前一天。
        day = datetime.now(timezone.utc).astimezone().date() - timedelta(days=1)
    start = f"{day.isoformat()}T00:00:00+08:00"
    next_day = day.fromordinal(day.toordinal() + 1)
    end = f"{next_day.isoformat()}T00:00:00+08:00"
    return day.isoformat(), start, end
