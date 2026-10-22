"""问答流程编排：混合检索 → 上下文组装 → LLM/模板回答 → 引用 → 会话落库。

引用始终来自检索命中而非模型输出，保证每条来源可追溯；
LLM 配置缺失或调用失败时自动降级本地模板，链路永远可用。
"""

from datetime import datetime, timezone
from uuid import uuid4

from . import db
from .llm import generate_answer
from .retrieval import hybrid_search

# 上下文最多携带的 chunk 数：过多会稀释重点，过少丢依据。
MAX_CONTEXT_CHUNKS = 4


def _step(name: str, detail: str, status: str = "done", provider: str = "local") -> dict:
    """统一生成可展示在前端时间线中的处理步骤。"""
    return {"name": name, "status": status, "detail": detail, "provider": provider}


def chat(message: str, conversation_id: str | None = None) -> dict:
    """执行一次完整问答并持久化会话；conversation_id 由前端续传时复用。"""
    started = datetime.now(timezone.utc)
    conversation_id = conversation_id or f"conv-{uuid4().hex[:8]}"

    hits, mode = hybrid_search(message)
    retrieval_mode = {"hybrid": "向量+关键词混合", "keyword-only": "关键词（向量模型未就绪）", "empty": "知识库为空"}[mode]
    trace = [
        _step("文档准备", "从 SQLite 知识库加载文档与 chunk"),
        _step("文本切分", "chunk 已在入库时完成切分与向量化"),
        _step("建立索引/检索", f"{retrieval_mode}，召回 {len(hits)} 个片段", provider=f"retrieval:{mode}"),
    ]

    top_hits = hits[:MAX_CONTEXT_CHUNKS]
    if top_hits:
        context = "\n---\n".join(hit["content"] for hit in top_hits)
        trace.append(_step("组装上下文", f"已组装 {len(top_hits)} 个高分 chunk"))
    else:
        context = ""
        trace.append(_step("组装上下文", "没有达到最低匹配条件，未组装上下文", "skipped"))

    # 回答生成：LLM 可用则真实生成，否则模板兜底；引用与 provider 一并返回。
    answer, provider = generate_answer(message, context, top_hits)
    trace.append(_step(
        "生成回答",
        "LLM 依据检索资料生成回答" if provider == "openai-compatible" else "本地模板生成（未配置 LLM 或调用失败）",
        provider=provider,
    ))

    citations = [
        {
            "document_id": hit["document_id"],
            "title": hit["title"],
            "source": hit["source"],
            "chunk_id": hit["chunk_id"],
            "score": hit["score"],
            "vector_score": hit["vector_score"],
            "keyword_score": hit["keyword_score"],
            "content": hit["content"],
        }
        for hit in hits
    ]
    trace.append(_step("引用来源", f"返回 {len(citations)} 条来源引用"))
    trace.append(_step("会话记录", "会话已持久化到 SQLite，重启不丢失"))

    # 会话与消息全部落库；citations 随助手消息一并保存，历史页可完整回看。
    db.append_message(conversation_id, "user", message, [])
    db.append_message(conversation_id, "assistant", answer, citations)

    return {
        "conversation_id": conversation_id,
        "answer": answer,
        "citations": citations,
        "context": context,
        "trace": trace,
        "provider": provider,
        "retrieval_mode": mode,
        "created_at": started.isoformat(),
    }
