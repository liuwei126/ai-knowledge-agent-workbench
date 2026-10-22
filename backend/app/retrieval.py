"""检索层：本地向量召回（fastembed）+ 关键词召回的混合检索。

- Embedding 使用 BAAI/bge-small-zh-v1.5（ONNX，约 100MB，中文效果好、无需 GPU）；
- 模型下载/加载失败时自动降级为纯关键词检索，保证系统始终可用；
- 混合得分 = 0.6 * 归一化向量相似度 + 0.4 * 归一化关键词得分（Demo 经验权重）。
"""

import logging
import math
import re
from typing import Any

from . import db

logger = logging.getLogger(__name__)

# 国内网络下 huggingface 直连不稳定，优先走 hf-mirror 镜像；可被环境变量覆盖。
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
VECTOR_WEIGHT = 0.6
KEYWORD_WEIGHT = 0.4

# 模型实例全局懒加载：首次调用时才下载/加载，之后的请求复用。
_embedding_model: Any = None
_model_failed = False


def _get_model() -> Any:
    """加载 fastembed 模型；失败时记录并置失败标记，后续调用不再重试。"""
    global _embedding_model, _model_failed
    if _embedding_model is not None or _model_failed:
        return _embedding_model
    try:
        from fastembed import TextEmbedding
        _embedding_model = TextEmbedding(model_name=MODEL_NAME)
        logger.info("向量模型加载成功：%s", MODEL_NAME)
    except Exception as exc:  # 模型下载失败/无网络等情况，降级为关键词检索
        _model_failed = True
        logger.warning("向量模型不可用（%s），检索降级为纯关键词模式", exc)
    return _embedding_model


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """批量向量化；模型不可用时返回 None（调用方据此决定是否跳过向量写入）。"""
    model = _get_model()
    if model is None or not texts:
        return None
    try:
        return [list(map(float, vec)) for vec in model.embed(texts)]
    except Exception as exc:
        logger.warning("向量化失败：%s", exc)
        return None


def is_vector_ready() -> bool:
    """供健康检查和 trace 展示当前检索能力。"""
    return _get_model() is not None


def rebuild_embeddings(batch_size: int = 64) -> int:
    """为所有缺失向量的 chunk 补算 embedding；返回处理的 chunk 数。"""
    model = _get_model()
    if model is None:
        return 0
    chunks = [c for c in db.list_chunks() if c["embedding"] is None]
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embed_texts([c["content"] for c in batch])
        if vectors is None:
            break
        # 借用 replace_chunks 整篇覆盖太重，这里逐条更新 embedding 字段。
        import struct
        with db.get_conn() as conn:
            for chunk, vec in zip(batch, vectors):
                conn.execute(
                    "UPDATE chunks SET embedding = ? WHERE chunk_id = ?",
                    (struct.pack(f"<{len(vec)}f", *vec), chunk["chunk_id"]),
                )
    return len(chunks)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _query_terms(query: str) -> set[str]:
    """提取中英文检索词；中文使用二元词，避免单字命中造成大量噪声。"""
    raw_terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower())
    terms: set[str] = set()
    for term in raw_terms:
        if re.fullmatch(r"[\u4e00-\u9fff]+", term) and len(term) > 1:
            terms.update(term[i:i + 2] for i in range(len(term) - 1))
        else:
            terms.add(term)
    return terms


def _keyword_scores(query: str, chunks: list[dict[str, Any]]) -> dict[str, tuple[float, list[str]]]:
    """计算每个 chunk 的关键词得分，返回 {chunk_id: (score, matched_terms)}。"""
    terms = _query_terms(query)
    scores: dict[str, tuple[float, list[str]]] = {}
    if not terms:
        return scores
    for chunk in chunks:
        title_text = ((chunk.get("title") or "") + (chunk.get("category") or "")).lower()
        content_text = chunk["content"].lower()
        matches = [term for term in terms if term in title_text or term in content_text]
        title_matches = [term for term in terms if term in title_text]
        if not matches:
            continue
        coverage = len(matches) / len(terms)
        title_bonus = min(len(title_matches) * 0.1, 0.2)
        scores[chunk["chunk_id"]] = (min(coverage + title_bonus, 1.0), matches)
    return scores


def hybrid_search(query: str, limit: int = 5) -> tuple[list[dict[str, Any]], str]:
    """混合检索：关键词 + 向量加权融合。

    返回 (结果列表, 检索模式)。模式用于前端 trace 展示：
    - hybrid：向量与关键词均可用
    - keyword-only：向量模型不可用，纯关键词
    """
    chunks = db.list_chunks()
    if not chunks:
        return [], "empty"

    kw_scores = _keyword_scores(query, chunks)

    # 向量召回：对查询向量化后与全部 chunk 算余弦。
    vec_scores: dict[str, float] = {}
    mode = "hybrid"
    query_vecs = embed_texts([query]) if chunks else None
    if query_vecs:
        qvec = query_vecs[0]
        for chunk in chunks:
            if chunk["embedding"]:
                vec_scores[chunk["chunk_id"]] = _cosine(qvec, chunk["embedding"])
    else:
        mode = "keyword-only"

    # 归一化后加权融合；两个分数都为 0 的 chunk 不进入结果。
    combined: list[dict[str, Any]] = []
    for chunk in chunks:
        cid = chunk["chunk_id"]
        kw = kw_scores.get(cid, (0.0, []))[0]
        vec = vec_scores.get(cid, 0.0)
        score = VECTOR_WEIGHT * vec + KEYWORD_WEIGHT * kw
        if score <= 0.01:
            continue
        matched_terms = kw_scores.get(cid, (0.0, []))[1]
        combined.append({
            "chunk_id": cid,
            "document_id": chunk["document_id"],
            "title": chunk.get("title", ""),
            "category": chunk.get("category", ""),
            "source": chunk.get("source", ""),
            "content": chunk["content"],
            "score": round(score, 4),
            "vector_score": round(vec, 4),
            "keyword_score": round(kw, 4),
            "matched_terms": matched_terms,
        })

    combined.sort(key=lambda item: item["score"], reverse=True)
    return combined[: max(1, min(limit, 20))], mode


# 兼容旧接口名：main.py 与 chat.py 统一走 hybrid_search。
def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    return hybrid_search(query, limit=limit)[0]
