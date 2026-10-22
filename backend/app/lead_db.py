"""线索分级数据层：leads 表建表、样例数据与读写。

表设计要点：
- 原始线索字段（公司名/规模/行业等）直接建列，便于 SQL 过滤；
- Agent 产出（维度得分/理由/销售要点）用 JSON 列存，结构随评分
  配置演进，不需要频繁改表——这是"稳定事实 + 可变结论"的常见拆法；
- status 区分 pending（待分析）/ scored（已分级）/ info_incomplete（信息不足），
  让"信息不足"成为一等公民，而不是伪装成低分。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id              TEXT PRIMARY KEY,
    company         TEXT NOT NULL,
    industry        TEXT NOT NULL,
    scale           INTEGER NOT NULL,
    region          TEXT NOT NULL,
    business        TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',
    extra_json      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    total_score     REAL,
    priority        TEXT,
    dim_scores_json TEXT,
    reasons_json    TEXT,
    talk_points_json TEXT,
    created_at      TEXT NOT NULL,
    scored_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_priority ON leads(priority);
"""

# 样例线索：覆盖"高/中/低匹配 + 信息不足"多种情况，方便演示分级效果。
_SAMPLE_LEADS = [
    ("lead-001", "智造科技（上海）有限公司", "制造业", 1200, "上海", "工业设备制造与售后运维服务，正在推进产线数字化改造"),
    ("lead-002", "华东冷链物流集团", "物流", 3000, "上海", "冷链仓储与运输，自有 40 个分拨中心，关注仓配一体化"),
    ("lead-003", "康宁医疗器械（苏州）", "医疗器械", 1500, "苏州", "二类三类医疗器械生产，有质检与经销商管理需求"),
    ("lead-004", "绿源新能源材料", "新能源", 2200, "宁波", "锂电池材料研发制造，多基地排产协同"),
    ("lead-005", "蓝海跨境电商", "零售", 800, "深圳", "跨境电商平台，海外仓与多渠道门店运营"),
    ("lead-006", "极速出行平台", "互联网", 5000, "北京", "网约车出行平台，运力调度与司机管理"),
    ("lead-007", "新乡三阳光电", "制造业", 260, "新乡", "光学镜片加工，以代工为主"),
    ("lead-008", "环球咨询上海分部", "专业服务", 150, "上海", "管理咨询服务，轻资产运营"),
    ("lead-009", "满堂红餐饮管理", "餐饮", 80, "成都", "连锁餐饮门店运营，共 6 家门店"),
    ("lead-010", "老王五金门市部", "零售", 3, "佛山", "五金工具零售门店"),
]


def init_lead_tables() -> None:
    """建 leads 表；幂等，启动时调用。表结构独立于知识库表，便于整体替换。"""
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def seed_leads() -> int:
    """写入样例线索（幂等：已有数据则跳过）。返回本次新写入条数。"""
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        (count,) = conn.execute("SELECT COUNT(*) FROM leads").fetchone()
        if count:
            return 0
        for row in _SAMPLE_LEADS:
            conn.execute(
                "INSERT INTO leads (id, company, industry, scale, region, business, source, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'demo-seed', 'pending', ?)",
                (*row, _utc_now()),
            )
    return len(_SAMPLE_LEADS)


def _conn() -> sqlite3.Connection:
    """复用 db.py 的数据库文件与目录，但连接独立建立，避免循环依赖。"""
    from . import db

    db.DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(db.DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def list_leads(status: str | None = None, priority: str | None = None) -> list[dict[str, Any]]:
    """按状态/优先级过滤线索列表，按得分降序。"""
    query = "SELECT * FROM leads WHERE 1=1"
    params: list[Any] = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if priority:
        query += " AND priority = ?"
        params.append(priority)
    query += " ORDER BY COALESCE(total_score, -1) DESC, created_at"
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    return [_row_to_lead(row) for row in rows]


def get_lead(lead_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return _row_to_lead(row) if row else None


def pending_leads(limit: int = 20) -> list[dict[str, Any]]:
    """取待分析线索，单批有上限，防止一次跑爆 Token 预算。"""
    with _conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM leads WHERE status = 'pending' ORDER BY created_at LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_lead(row) for row in rows]


def save_lead_result(lead_id: str, result: dict[str, Any]) -> None:
    """写回单条线索的分析结论（得分/理由/要点/状态）。

    信息不足时 total_score 为 null、status=info_incomplete，
    查询排序时 COALESCE 保证它排在最后。
    """
    with _conn() as conn:
        conn.execute(
            """
            UPDATE leads
            SET status = ?,
                total_score = ?,
                priority = ?,
                dim_scores_json = ?,
                reasons_json = ?,
                talk_points_json = ?,
                extra_json = ?,
                scored_at = ?
            WHERE id = ?
            """,
            (
                result["status"],
                result.get("total_score"),
                result.get("priority"),
                _json(result.get("dim_scores")),
                _json(result.get("reasons")),
                _json(result.get("talk_points")),
                _json(result.get("extra")),
                _utc_now(),
                lead_id,
            ),
        )


def _row_to_lead(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "company": row["company"],
        "industry": row["industry"],
        "scale": row["scale"],
        "region": row["region"],
        "business": row["business"],
        "source": row["source"],
        "status": row["status"],
        "total_score": row["total_score"],
        "priority": row["priority"],
        "dim_scores": json.loads(row["dim_scores_json"]) if row["dim_scores_json"] else None,
        "reasons": json.loads(row["reasons_json"]) if row["reasons_json"] else None,
        "talk_points": json.loads(row["talk_points_json"]) if row["talk_points_json"] else None,
        "extra": json.loads(row["extra_json"]) if row["extra_json"] else None,
        "created_at": row["created_at"],
        "scored_at": row["scored_at"],
    }


def _json(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None
