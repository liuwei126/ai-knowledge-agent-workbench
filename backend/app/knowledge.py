"""文档接入层：格式解析、文本切分、文档增删，全部落 SQLite。

上传侧支持 .txt / .md / .pdf / .docx；解析出的纯文本统一走同一条
切分 → 入库 → 向量化链路，检索层不感知原始文件格式。
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import db

# 仅为学习演示准备的虚构样例；首次启动写入数据库，不代表真实内部制度。
DEFAULT_DOCUMENTS = [
    {"id": "travel-expense", "title": "差旅费用报销指引", "category": "财务制度", "content": "员工因公出差产生的交通费、住宿费和伙食补助，可在出差结束后 10 个工作日内提交报销。高铁二等座和经济舱为默认标准，超标准部分需事前审批。报销时需上传发票、行程单和审批记录。", "source": "mock://差旅费用报销指引"},
    {"id": "leave-policy", "title": "员工请假办理说明", "category": "人事制度", "content": "请假应至少提前 1 个工作日在企业协同平台提交申请，填写请假类型、起止时间和工作交接人。直属主管审批后方可休假，超过 3 个工作日的请假还需部门负责人审批。紧急情况应先电话报备并在返岗后补流程。", "source": "mock://员工请假办理说明"},
    {"id": "it-account", "title": "IT 账号与权限使用规范", "category": "信息安全", "content": "员工入职后由直属主管发起 IT 账号申请，信息技术部通常在 1 个工作日内开通。不得共享密码或使用他人账号，密码遗失应立即联系服务台重置。离职或转岗时，原权限会按流程回收。", "source": "mock://IT 账号与权限使用规范"},
    {"id": "expense-approval", "title": "日常费用审批规则", "category": "财务制度", "content": "日常采购和业务费用应先申请后支出。金额 5000 元以内由部门负责人审批，5000 元至 50000 元需分管领导审批，超过 50000 元需按公司授权矩阵追加审批。紧急采购也应在事后 2 个工作日内补齐流程。", "source": "mock://日常费用审批规则"},
    # 工单 Agent 会检索这份规则，用来说明“数据工具 + RAG”如何组合。
    {"id": "ticket-escalation", "title": "工单升级与处理规范", "category": "服务台规范", "content": "高优先级工单应在 30 分钟内确认负责人，并在 2 小时内向业务方同步处理进展；涉及核心系统不可用的工单，应立即通知值班负责人。未解决工单需要在日报中列出当前状态、负责人和预计完成时间。", "source": "mock://工单升级与处理规范"},
]

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def extract_text(filename: str, raw: bytes) -> str:
    """按扩展名把上传文件解析成纯文本；不认识的格式在上游路由就已被拒绝。

    PDF 用 PyMuPDF 逐页抽取并按页拼接；DOCX 只取段落文本（表格文字暂不解析，
    如有需要可在生产版补充）；文本文件按 UTF-8 解码，非法字节替换为占位符。
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        import fitz  # PyMuPDF
        parts = []
        with fitz.open(stream=raw, filetype="pdf") as pdf:
            for page in pdf:
                parts.append(page.get_text("text"))
        return "\n".join(parts)
    if suffix == ".docx":
        import io
        import docx
        document = docx.Document(io.BytesIO(raw))
        return "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
    # .txt / .md
    return raw.decode("utf-8", errors="replace")


def split_text(text: str, size: int = 220, overlap: int = 40) -> list[str]:
    """按段落切分，超长段落定长拆分并带重叠窗口，避免句子被拦腰截断后丢失上下文。

    overlap 让相邻 chunk 共享末尾/开头的一小段文字，提高语义连续性；
    相比旧版（180 字硬切、无重叠），长制度条款的召回质量更稳定。
    """
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= size:
            chunks.append(paragraph)
        else:
            step = size - overlap
            for i in range(0, len(paragraph), step):
                piece = paragraph[i:i + size]
                if piece.strip():
                    chunks.append(piece)
                if i + size >= len(paragraph):
                    break
    return chunks or [text.strip()]


def seed_default_documents() -> None:
    """写入缺失的默认文档并完成向量化。

    不能只判断“数据库是否为空”：项目升级后可能新增默认文档，
    这时要按稳定 ID 补齐，避免旧数据库永远缺少新资料。
    """
    for doc in DEFAULT_DOCUMENTS:
        if db.get_document(doc["id"]):
            continue
        add_document(doc["title"], doc["content"], category=doc["category"],
                     doc_id=doc["id"], source=doc["source"], doc_type="txt")


def add_document(
    filename: str,
    content: str,
    category: str = "用户上传",
    doc_id: str | None = None,
    source: str | None = None,
    doc_type: str = "txt",
    size_bytes: int = 0,
) -> tuple[dict[str, Any], int, bool]:
    """新增文档：写库 → 切分 → 尝试向量化。

    返回 (文档, chunk 数, 是否已向量化)。向量化失败（模型未就绪等）不阻塞
    上传，关键词检索仍然可用；重新向量化可调用 retrieval.rebuild_embeddings。
    """
    from .retrieval import embed_texts  # 延迟导入，避免循环依赖

    document = {
        "id": doc_id or f"upload-{uuid4().hex[:8]}",
        "title": Path(filename).stem,
        "category": category,
        "content": content,
        "source": source or f"upload://{filename}",
        "doc_type": doc_type,
        "size_bytes": size_bytes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    db.insert_document(document)
    chunks = [
        {
            "chunk_id": f"{document['id']}-{i + 1}",
            "document_id": document["id"],
            "content": chunk,
            "index": i + 1,
        }
        for i, chunk in enumerate(split_text(content))
    ]
    embeddings = embed_texts([c["content"] for c in chunks])
    db.replace_chunks(document["id"], chunks, embeddings)
    return document, len(chunks), embeddings is not None


def delete_document(document_id: str) -> bool:
    """删除文档（chunk 外键级联），返回是否存在。"""
    return db.delete_document(document_id)


def list_documents() -> list[dict[str, Any]]:
    return db.list_documents()
