"""线索分级 Agent 编排层：收集 → 硬维度评分 → 软维度评分 → 分级 → 销售要点。

核心设计（对应"销售数据收集与分析"场景的标准做法）：

1. 流程固定，代码串行编排——不用自由 Agent 循环，每步输入输出明确；
2. 硬维度（行业/规模/地区）由代码按 ICP 配置算分，零幻觉、可解释；
3. 软维度（业务相关度）交给 LLM 按 rubric 打分，未配置模型时降级为
   关键词计数——打分逻辑降级但不缺位；
4. 信息不足的线索标记 info_incomplete，不硬打分，避免"垃圾进垃圾出"；
5. 每步写 trace，结果带理由和证据，销售看得懂才敢用。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lead_config_loader import load_lead_config
from .lead_db import get_lead, pending_leads, save_lead_result
from .lead_tools import enrich_company
from .llm import get_llm_config

logger = logging.getLogger(__name__)

CONFIG = load_lead_config()


def analyze_leads(lead_ids: list[str] | None = None, max_items: int | None = None) -> dict[str, Any]:
    """入口：分析指定线索；不传 ID 则处理全部 pending 线索（限量）。"""
    started = datetime.now(timezone.utc)
    limit = min(max_items or CONFIG["pipeline"]["max_batch"], CONFIG["pipeline"]["max_batch"])

    if lead_ids:
        leads = [lead for lead in (get_lead(i) for i in lead_ids) if lead is not None]
    else:
        leads = pending_leads(limit=limit)

    results = []
    for lead in leads:
        try:
            results.append(_analyze_one(lead))
        except Exception as exc:  # 单条失败不阻塞整批，其余线索继续。
            logger.exception("线索分析失败 lead_id=%s", lead["id"])
            results.append({"lead_id": lead["id"], "company": lead["company"], "error": str(exc)})

    high = sum(1 for r in results if r.get("priority") == "HIGH")
    mid = sum(1 for r in results if r.get("priority") == "MID")
    low = sum(1 for r in results if r.get("priority") == "LOW")
    incomplete = sum(1 for r in results if r.get("status") == "info_incomplete")

    return {
        "analyzed": len(results),
        "summary": {"HIGH": high, "MID": mid, "LOW": low, "info_incomplete": incomplete},
        "items": results,
        "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }


def _analyze_one(lead: dict[str, Any]) -> dict[str, Any]:
    """单条线索的完整 pipeline，返回结论并落库。"""
    trace: list[dict[str, Any]] = []

    # ① 数据收集：库里已有字段 + 工具补充公开信息。
    enriched = enrich_company(lead["company"])
    extra = enriched.get("extra") or {}
    profile = {**lead, "extra": extra}
    trace.append({
        "name": "数据收集",
        "status": "done" if enriched["found"] else "skipped",
        "detail": f"补充信息：{'命中 ' + ','.join(enriched['sources']) if enriched['found'] else '公开渠道未命中'}",
        "provider": f"tool:enrich_company({enriched['request_id']})",
    })

    # 关键字段缺失 → 标记信息不足，不进入打分（防垃圾进垃圾出）。
    if not lead["industry"] or not lead["scale"]:
        result = {
            "status": "info_incomplete", "total_score": None, "priority": None,
            "dim_scores": None, "reasons": ["关键信息缺失（行业或规模），需人工补充后再分析"],
            "talk_points": None, "extra": extra,
        }
        save_lead_result(lead["id"], result)
        trace.append({"name": "完整性检查", "status": "blocked", "detail": "行业/规模缺失，跳过打分", "provider": "rule"})
        return {**{"lead_id": lead["id"], "company": lead["company"]}, **result, "trace": trace}

    # ② 硬维度评分：纯代码按配置算，零幻觉。
    dim_scores, reasons = _score_hard_dimensions(profile)
    provider = "rule-based"
    trace.append({
        "name": "硬维度评分",
        "status": "done",
        "detail": f"行业 {dim_scores['industry']} / 规模 {dim_scores['scale']} / 地区 {dim_scores['region']}",
        "provider": provider,
    })

    # ③ 软维度评分：LLM rubric 打分，失败降级关键词计数。
    relevance, relevance_reason, soft_provider = _score_relevance(profile)
    dim_scores["business_relevance"] = relevance * CONFIG["weights"]["business_relevance"] // 10
    reasons.append(relevance_reason)
    trace.append({
        "name": "软维度评分",
        "status": "done",
        "detail": f"业务相关度 {relevance}/10：{relevance_reason[:40]}",
        "provider": soft_provider,
    })

    # ④ 分级 + 销售要点。
    total = sum(dim_scores.values())
    priority = _grade(total)
    talk_points = _talk_points(profile, priority, dim_scores)
    provider = f"{provider}+{soft_provider}"
    trace.append({
        "name": "分级与要点",
        "status": "done",
        "detail": f"总分 {total} → {priority}，生成 {len(talk_points)} 条跟进要点",
        "provider": "rule+llm" if "llm" in soft_provider else "rule+template",
    })

    result = {
        "status": "scored", "total_score": total, "priority": priority,
        "dim_scores": dim_scores, "reasons": reasons,
        "talk_points": talk_points, "extra": extra,
    }
    save_lead_result(lead["id"], result)
    return {**{"lead_id": lead["id"], "company": lead["company"]}, **result, "trace": trace}


# ---------- 硬维度：代码算分 ----------

def _score_hard_dimensions(profile: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """行业/规模/地区三个硬维度按 ICP 配置打分，每条结论都附带理由。"""
    icp = CONFIG["icp"]
    weights = CONFIG["weights"]
    scores: dict[str, int] = {}
    reasons: list[str] = []

    industry = profile["industry"]
    if industry in icp["target_industries"]:
        scores["industry"] = weights["industry"]
        reasons.append(f"行业「{industry}」是目标行业，得满分 {weights['industry']} 分")
    elif any(adj in industry for adj in icp["adjacent_industries"]):
        scores["industry"] = weights["industry"] // 2
        reasons.append(f"行业「{industry}」属于邻近行业（可归入目标行业），得 {weights['industry'] // 2} 分")
    else:
        scores["industry"] = 0
        reasons.append(f"行业「{industry}」不在目标行业清单，得 0 分")

    low, high = icp["scale_range"]
    scale = int(profile["scale"])
    if low <= scale <= high:
        scores["scale"] = weights["scale"]
        reasons.append(f"规模 {scale} 人处于目标区间 [{low}, {high}]，得满分 {weights['scale']} 分")
    elif scale >= low * 0.5:
        scores["scale"] = weights["scale"] // 2
        reasons.append(f"规模 {scale} 人略低于目标区间下限，得 {weights['scale'] // 2} 分")
    else:
        scores["scale"] = 0
        reasons.append(f"规模 {scale} 人远低于目标区间，得 0 分")

    region = profile["region"]
    if any(city in region for city in icp["target_regions"]):
        scores["region"] = weights["region"]
        reasons.append(f"所在地区「{region}」在重点覆盖区域，得满分 {weights['region']} 分")
    else:
        scores["region"] = 5
        reasons.append(f"所在地区「{region}」非重点区域，仅得保底 5 分")

    return scores, reasons


def _grade(total: float) -> str:
    """总分 → 优先级；阈值读配置，换客户只改配置不改代码。"""
    if total >= CONFIG["thresholds"]["high"]:
        return "HIGH"
    if total >= CONFIG["thresholds"]["mid"]:
        return "MID"
    return "LOW"


# ---------- 软维度：LLM rubric，失败降级 ----------

def _score_relevance(profile: dict[str, Any]) -> tuple[int, str, str]:
    """业务相关度 0-10 分。返回 (分数, 理由, provider)。"""
    config = get_llm_config()
    if config["configured"]:
        try:
            return _relevance_by_llm(config, profile)
        except Exception as exc:
            logger.warning("相关度 LLM 评分失败，降级关键词匹配：%s", exc)
    return _relevance_by_keywords(profile)


def _relevance_by_llm(config: dict[str, str | None], profile: dict[str, Any]) -> tuple[int, str, str]:
    """让 LLM 按 rubric 对主营业务与 ICP 的契合度打分，强制 JSON 输出。"""
    import httpx

    icp = CONFIG["icp"]
    system = (
        "你是 B2B 销售线索评估助手。请根据理想客户画像，对线索主营业务的相关度打分。"
        "评分标准(rubric)：9-10 痛点与产品高度契合且近期有相关动作；"
        "6-8 有明确相关业务但未提及改造意愿；3-5 仅部分沾边；0-2 基本无关。"
        "只输出 JSON：{\"relevance\": <0-10整数>, \"reason\": \"<一句话理由>\"}，不要输出其他内容。"
    )
    user = (
        f"理想客户画像：目标行业 {icp['target_industries']}，关注关键词 {icp['relevance_keywords']}。\n"
        f"线索：{profile['company']}，行业 {profile['industry']}，"
        f"主营业务：{profile['business']}。\n"
        f"公开信息：{json.dumps(profile.get('extra') or {}, ensure_ascii=False)}"
    )
    response = httpx.post(
        f"{config['base_url'].rstrip('/')}/chat/completions",  # type: ignore[union-attr]
        headers={"Authorization": f"Bearer {config['api_key']}"},
        json={
            "model": config["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": 200,
        },
        timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
    )
    response.raise_for_status()
    content = (response.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    data = _parse_json(content)
    relevance = max(0, min(10, int(data["relevance"])))
    return relevance, str(data.get("reason", "")), "llm-rubric"


def _relevance_by_keywords(profile: dict[str, Any]) -> tuple[int, str, str]:
    """无模型时的降级打分：ICP 关键词在主营业务里的命中数 × 2，封顶 10。"""
    hits = [kw for kw in CONFIG["icp"]["relevance_keywords"] if kw in profile["business"]]
    score = min(10, len(hits) * 2)
    return score, f"主营业务命中关键词：{'、'.join(hits) if hits else '无'}", "keyword-fallback"


def _parse_json(content: str) -> dict[str, Any]:
    """解析模型输出；容忍 markdown 代码块包裹。解析失败抛异常由上层降级。"""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise ValueError(f"模型输出不是合法 JSON：{content[:100]}")
    return json.loads(match.group())


# ---------- 销售要点 ----------

def _talk_points(profile: dict[str, Any], priority: str, dim_scores: dict[str, int]) -> list[str]:
    """为高/中优先级线索生成跟进要点；低优先级给一句维护建议即可。"""
    config = get_llm_config()
    if priority != "LOW" and config["configured"]:
        try:
            return _talk_points_by_llm(config, profile, priority)
        except Exception as exc:
            logger.warning("要点生成 LLM 调用失败，使用模板：%s", exc)
    return _talk_points_template(profile, priority, dim_scores)


def _talk_points_by_llm(config: dict[str, str | None], profile: dict[str, Any], priority: str) -> list[str]:
    """LLM 基于画像生成 3-5 条开场要点，同样强制 JSON。"""
    import httpx

    system = (
        "你是 B2B 销售顾问助手。请为销售生成联系该客户前的开场要点。"
        "要求：3-5 条、每条一句话、结合客户业务与公开信息、包含 1-2 个值得问的开放问题；"
        "不要编造客户内部信息。只输出 JSON：{\"points\": [\"...\"]}。"
    )
    user = (
        f"客户：{profile['company']}，行业 {profile['industry']}，规模 {profile['scale']} 人，"
        f"地区 {profile['region']}，主营业务：{profile['business']}，线索优先级 {priority}。"
        f"公开信息：{json.dumps(profile.get('extra') or {}, ensure_ascii=False)}"
    )
    response = httpx.post(
        f"{config['base_url'].rstrip('/')}/chat/completions",  # type: ignore[union-attr]
        headers={"Authorization": f"Bearer {config['api_key']}"},
        json={
            "model": config["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.4,
            "max_tokens": 500,
        },
        timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
    )
    response.raise_for_status()
    content = (response.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    points = _parse_json(content).get("points", [])
    return [str(p) for p in points][:5] or _talk_points_template(profile, priority, {})


def _talk_points_template(profile: dict[str, Any], priority: str, dim_scores: dict[str, int]) -> list[str]:
    """无模型时的模板要点：从画像和得分里提取事实组织话术。"""
    if priority == "LOW":
        return ["低优先级：暂不主动跟进，纳入季度邮件列表保持触达。"]
    points = [
        f"开场聚焦 {profile['industry']} 行业场景，提「{profile['business'][:20]}」相关痛点切入。",
    ]
    if profile.get("extra", {}).get("recent_news"):
        points.append(f"用公开动态破冰：{profile['extra']['recent_news']}。")
    if dim_scores.get("scale", 0) > 0:
        points.append(f"围绕 {profile['scale']} 人规模企业的协作效率问题，准备 1-2 个同行业案例。")
    points.append("开放问题：目前这类业务主要靠什么工具/流程支撑？最耗时间的环节是哪一步？")
    return points[:5]
