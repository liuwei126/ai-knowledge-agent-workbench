"""FastAPI 应用入口：HTTP 路由、参数校验边界与启动初始化。

路由层只做协议与校验，检索/问答/存储细节分别在 retrieval.py、
chat.py、db.py，便于后续替换向量库或数据库实现。
"""

import logging

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import db, knowledge
from .chat import chat
from .lead_agent import analyze_leads
from .lead_db import init_lead_tables, list_leads, seed_leads
from .llm import get_llm_config
from .models import ChatRequest, FeedbackRequest, LeadAnalyzeRequest, TicketReportRequest
from .retrieval import is_vector_ready
from .ticket_agent import classify_intent, run_ticket_agent
from .ticket_db import seed_tickets

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="AI Knowledge Agent Workbench",
    version="2.0.0",
    description="本地知识库问答系统：PDF/DOCX 上传、向量+关键词混合检索、LLM 生成（可降级）。",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """初始化数据库并写入默认样例文档；向量模型在首次检索时懒加载。"""
    db.init_db()
    knowledge.seed_default_documents()
    seed_tickets()
    init_lead_tables()
    seed_leads()


@app.get("/api/health")
def health():
    """健康检查：附带向量检索与 LLM 配置状态，便于前端展示当前能力。"""
    return {
        "status": "ok",
        "service": "ai-knowledge-agent-workbench",
        "vector_ready": is_vector_ready(),
        "llm_configured": get_llm_config()["configured"],
    }


@app.get("/api/documents")
def documents():
    """文档清单，附带每篇文档的真实 chunk 数；不回传全文只回传摘要。"""
    docs = knowledge.list_documents()
    items = []
    for doc in docs:
        chunk_count = len(db.list_chunks(doc["id"]))
        items.append({**{k: v for k, v in doc.items() if k != "content"},
                      "preview": doc["content"][:80], "chunks": chunk_count})
    return {"items": items, "total": len(items)}


@app.post("/api/documents/upload")
async def upload(file: UploadFile = File(...)):
    """上传文档：支持 .txt/.md/.pdf/.docx，单文件不超过 10MB。"""
    filename = file.filename or ""
    if not filename.lower().endswith(tuple(knowledge.SUPPORTED_EXTENSIONS)):
        raise HTTPException(400, "仅支持 " + " / ".join(sorted(knowledge.SUPPORTED_EXTENSIONS)) + " 文件")
    raw = await file.read()
    if len(raw) > knowledge.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "单个文档不能超过 10 MB")
    try:
        content = knowledge.extract_text(filename, raw).strip()
    except Exception as exc:
        raise HTTPException(400, f"文档解析失败：{exc}")
    if not content:
        raise HTTPException(400, "文档内容为空或无法提取文本（扫描版 PDF 暂不支持）")
    doc_type = filename.rsplit(".", 1)[-1].lower()
    document, chunk_count, embedded = knowledge.add_document(
        filename, content, doc_type=doc_type, size_bytes=len(raw),
    )
    return {
        "document": {"id": document["id"], "title": document["title"]},
        "chunks": chunk_count,
        "embedded": embedded,
        "message": f"文档已入库，切分 {chunk_count} 个片段" + ("，向量索引已建立" if embedded else "（向量模型未就绪，暂用关键词检索）"),
    }


@app.delete("/api/documents/{document_id}")
def remove_document(document_id: str):
    """删除文档及其全部 chunk；不存在的 ID 返回 404。"""
    if not knowledge.delete_document(document_id):
        raise HTTPException(404, "文档不存在")
    return {"ok": True, "message": "文档已删除"}


@app.get("/api/search")
def search_api(q: str = "", limit: int = 5):
    """调试检索接口：返回混合召回结果与得分构成。"""
    from .retrieval import hybrid_search
    items, mode = hybrid_search(q, limit=limit) if q.strip() else ([], "empty")
    return {"query": q, "items": items, "retrieval_mode": mode}


@app.post("/api/chat")
def chat_api(request: ChatRequest):
    # 保留原知识库问答接口；工单场景使用下面独立的报告接口，便于演示工具调用链。
    return chat(request.message, request.conversation_id)


@app.post("/api/ticket-report")
def ticket_report(request: TicketReportRequest):
    """生成工单简报：查询业务数据 → 检索规范 → 生成报告。"""
    return run_ticket_agent(request.message, request.date)


@app.post("/api/agent")
def agent(request: ChatRequest):
    """统一 Agent 入口：根据意图把请求路由到工单 Agent 或原知识库问答。"""
    if classify_intent(request.message) == "ticket_report":
        return run_ticket_agent(request.message)
    return chat(request.message, request.conversation_id)


@app.get("/api/tickets")
def tickets(date: str | None = None):
    """调试接口：直接查看某天工单，便于对照 Agent 报告中的统计结果。"""
    from .ticket_db import query_tickets, today_range
    _, start, end = today_range(date)
    items = query_tickets(start, end)
    return {"date": date, "items": items, "total": len(items)}


@app.post("/api/leads/analyze")
def leads_analyze(request: LeadAnalyzeRequest):
    """线索分级：收集信息 → 硬/软维度评分 → 分级 → 生成销售要点。"""
    return analyze_leads(lead_ids=request.lead_ids or None, max_items=request.max_items)


@app.get("/api/leads")
def leads(priority: str | None = None, status: str | None = None):
    """线索列表：可按优先级（HIGH/MID/LOW）或状态过滤，按得分降序。"""
    items = list_leads(status=status, priority=priority)
    return {"items": items, "total": len(items)}


@app.get("/api/conversations")
def get_conversations():
    """返回全部会话（已持久化，重启不丢）。"""
    return {"items": db.list_conversations()}


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str):
    conv = db.get_conversation(conversation_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    return conv


@app.post("/api/feedback")
def post_feedback(request: FeedbackRequest):
    item = db.insert_feedback(request.conversation_id, request.message_id,
                              request.rating, request.comment)
    return {"ok": True, "item": item}


@app.post("/api/reindex")
def reindex():
    """为缺失向量的 chunk 补算 embedding（例如模型首次下载完成或升级后）。"""
    from .retrieval import rebuild_embeddings
    count = rebuild_embeddings()
    return {"ok": True, "processed": count, "message": f"已为 {count} 个片段补算向量" if count else "没有需要补算的片段"}
