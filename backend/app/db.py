"""SQLite 持久化层：文档、chunk（含向量）、会话、反馈统一落库。

替换原 JSON + 内存方案，解决重启丢失会话/文档、并发写坏 JSON 两个问题。
仅使用标准库 sqlite3，单机 Demo 无需额外数据库服务；
生产环境可将本模块替换为 PostgreSQL + pgvector，接口保持不变。
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_FILE = DATA_DIR / "knowledge.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,
    doc_type    TEXT NOT NULL DEFAULT 'txt',
    size_bytes  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content      TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    embedding    BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    citations_json   TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS feedback (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT,
    message_id       TEXT,
    rating           TEXT NOT NULL,
    comment          TEXT,
    created_at       TEXT NOT NULL
);

-- 工单表：模拟 OA/服务台的业务数据，实际项目中可替换为 HTTP API 或 PostgreSQL。
CREATE TABLE IF NOT EXISTS tickets (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    department  TEXT NOT NULL,
    priority    TEXT NOT NULL,
    status      TEXT NOT NULL,
    assignee    TEXT NOT NULL,
    resolution  TEXT,
    created_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

-- 工具调用审计表：记录 Agent 调了什么工具、耗时和结果，便于排障与复盘。
CREATE TABLE IF NOT EXISTS tool_audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    input_json  TEXT NOT NULL,
    output_json TEXT,
    status      TEXT NOT NULL,
    elapsed_ms  INTEGER NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL
);
"""


def _now() -> str:
    """统一 UTC ISO 时间戳，展示端再转本地时区。"""
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    """获取启用外键的连接；SQLite 每次调用独立连接，避免跨线程复用问题。"""
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """建表；幂等，应用启动时调用。"""
    with get_conn() as conn:
        conn.executescript(_SCHEMA)


def _row_to_document(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "content": row["content"],
        "source": row["source"],
        "doc_type": row["doc_type"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"],
    }


# ---------- 文档 ----------

def insert_document(document: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, title, category, content, source, doc_type, size_bytes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document["id"], document["title"], document["category"],
                document["content"], document["source"], document.get("doc_type", "txt"),
                document.get("size_bytes", 0), document.get("created_at", _now()),
            ),
        )


def list_documents() -> list[dict[str, Any]]:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [_row_to_document(row) for row in rows]


def get_document(document_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return _row_to_document(row) if row else None


def delete_document(document_id: str) -> bool:
    """删除文档及其全部 chunk（外键级联）；返回是否存在。"""
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return cursor.rowcount > 0


# ---------- Chunk 与向量 ----------

def replace_chunks(document_id: str, chunks: list[dict[str, Any]], embeddings: list[list[float]] | None = None) -> None:
    """覆盖写入一篇文档的全部 chunk；embedding 用 float32 序列化成 BLOB。

    重算切分或向量时整篇覆盖，避免残留旧 chunk 造成"幽灵引用"。
    """
    import struct

    with get_conn() as conn:
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        for i, chunk in enumerate(chunks):
            blob = None
            if embeddings is not None and i < len(embeddings):
                # struct 打包比 json 紧凑得多；读取时按 float32 解包。
                blob = struct.pack(f"<{len(embeddings[i])}f", *embeddings[i])
            conn.execute(
                "INSERT INTO chunks (chunk_id, document_id, content, chunk_index, embedding)"
                " VALUES (?, ?, ?, ?, ?)",
                (chunk["chunk_id"], document_id, chunk["content"], chunk["index"], blob),
            )


def list_chunks(document_id: str | None = None) -> list[dict[str, Any]]:
    """返回 chunk 列表；embedding 字段为原始 BLOB（可能为 None），由检索层解码。"""
    import struct

    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        if document_id:
            rows = conn.execute("SELECT * FROM chunks WHERE document_id = ?", (document_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM chunks").fetchall()

    results = []
    for row in rows:
        item = {
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "content": row["content"],
            "index": row["chunk_index"],
            "embedding": None,
        }
        if row["embedding"] is not None:
            n = len(row["embedding"]) // 4
            item["embedding"] = list(struct.unpack(f"<{n}f", row["embedding"]))
        # 带上文档元数据，便于检索结果直接返回标题/分类/来源。
        doc = get_document(row["document_id"])
        if doc:
            item.update({"title": doc["title"], "category": doc["category"], "source": doc["source"]})
        results.append(item)
    return results


def count_chunked_documents() -> int:
    """用于判断默认文档是否已初始化过，避免重复写库。"""
    with get_conn() as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    return count


# ---------- 会话 ----------

def upsert_conversation(conversation_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, created_at) VALUES (?, ?)",
            (conversation_id, _now()),
        )


def append_message(conversation_id: str, role: str, content: str, citations: list | None = None) -> None:
    upsert_conversation(conversation_id)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, citations_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, content, json.dumps(citations or [], ensure_ascii=False), _now()),
        )


def list_conversations() -> list[dict[str, Any]]:
    """返回会话索引及各会话消息（会话数量少时全量返回即可）。"""
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        convs = conn.execute("SELECT * FROM conversations ORDER BY created_at DESC").fetchall()
        items = []
        for conv in convs:
            msgs = conn.execute(
                "SELECT role, content, citations_json, created_at FROM messages"
                " WHERE conversation_id = ? ORDER BY id",
                (conv["id"],),
            ).fetchall()
            messages = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "citations": json.loads(m["citations_json"] or "[]"),
                    "created_at": m["created_at"],
                }
                for m in msgs
            ]
            items.append({"id": conv["id"], "created_at": conv["created_at"], "messages": messages})
    return items


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    items = [c for c in list_conversations() if c["id"] == conversation_id]
    return items[0] if items else None


# ---------- 反馈 ----------

def insert_feedback(conversation_id: str | None, message_id: str | None, rating: str, comment: str | None) -> dict[str, Any]:
    item = {
        "id": None, "conversation_id": conversation_id, "message_id": message_id,
        "rating": rating, "comment": comment, "created_at": _now(),
    }
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO feedback (conversation_id, message_id, rating, comment, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (conversation_id, message_id, rating, comment, item["created_at"]),
        )
        item["id"] = cursor.lastrowid
    return item
