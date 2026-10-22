"""工单 Agent 编排层：意图识别 → 工具调用 → 知识检索 → 报告生成。

这里故意采用“确定性路由 + 可选 LLM 总结”，而不是把所有决策都交给模型：
- 是否调用工单工具由代码判断，关键工具不会因为模型漏判而不执行；
- LLM 只负责把已经统计好的事实组织成自然语言；
- 没有 LLM 时使用模板兜底，方便本地学习和演示。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .llm import get_llm_config
from .retrieval import hybrid_search
from .ticket_tools import query_yesterday_tickets

logger = logging.getLogger(__name__)


REPORT_KEYWORDS = ("工单", "周报", "日报", "昨日", "昨天", "领导", "问题汇总")


def classify_intent(message: str) -> str:
    """最小可用意图路由器。

    面试时可以说明：工具数量少、规则稳定时，规则路由比让 LLM 自由选择更可控；
    工具数量变多后，可以升级成 LLM Router，但仍要保留权限和参数校验。
    """
    text = message.lower()
    if any(keyword in text for keyword in REPORT_KEYWORDS):
        return "ticket_report"
    return "knowledge_qa"


def run_ticket_agent(message: str, date_text: str | None = None) -> dict[str, Any]:
    """运行工单总结 Agent，并返回可展示的 trace。"""
    started = datetime.now(timezone.utc)
    trace: list[dict[str, Any]] = []

    trace.append({
        "name": "意图识别",
        "status": "done",
        "detail": "识别为工单总结/统计任务",
        "provider": "rule-router",
    })

    # 关键业务工具由代码显式调用，避免模型“应该调用但没有调用”。
    ticket_data = query_yesterday_tickets(date_text)
    trace.append({
        "name": "查询工单工具",
        "status": "done",
        "detail": f"查询 {ticket_data['date']} 工单，共 {ticket_data['summary']['total']} 条",
        "provider": "tool:query_yesterday_tickets",
    })

    # 通过知识库补充处理规范/升级规则；没有命中时也不影响工单事实统计。
    policy_hits, retrieval_mode = hybrid_search("高优先级工单 30 分钟 2 小时 未解决 日报 处理规范", limit=2)
    policy_context = "\n".join(hit["content"] for hit in policy_hits)
    trace.append({
        "name": "检索处理规范",
        "status": "done" if policy_hits else "skipped",
        "detail": f"召回 {len(policy_hits)} 条知识库片段",
        "provider": f"retrieval:{retrieval_mode}",
    })

    answer, provider = generate_ticket_report(message, ticket_data, policy_context)
    trace.append({
        "name": "生成工单简报",
        "status": "done",
        "detail": "已根据结构化工单事实生成报告" if provider != "fallback-template" else "未配置 LLM，使用本地模板生成",
        "provider": provider,
    })
    trace.append({
        "name": "审计记录",
        "status": "done",
        "detail": "工具调用已写入 tool_audit_logs",
        "provider": "sqlite",
    })

    return {
        "intent": "ticket_report",
        "date": ticket_data["date"],
        "answer": answer,
        "summary": ticket_data["summary"],
        "tickets": ticket_data["tickets"],
        "policy_hits": policy_hits,
        "trace": trace,
        "provider": provider,
        "elapsed_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }


def generate_ticket_report(
    request: str,
    ticket_data: dict[str, Any],
    policy_context: str,
) -> tuple[str, str]:
    """优先调用 OpenAI-compatible LLM，失败则回退到确定性模板。"""
    config = get_llm_config()
    if config["configured"]:
        try:
            answer = _call_report_llm(config, request, ticket_data, policy_context)
            if answer:
                return answer, "openai-compatible"
        except Exception as exc:
            logger.warning("工单报告 LLM 调用失败，使用模板兜底：%s", exc)
    return _template_report(ticket_data), "fallback-template"


def _call_report_llm(config: dict[str, str | None], request: str, ticket_data: dict[str, Any], policy_context: str) -> str:
    """调用兼容 OpenAI Chat Completions 的接口。

    注意：只把必要字段传给模型；真正的工单明细可按生产需要脱敏。
    """
    import httpx
    import json

    system = (
        "你是企业工单运营助手。只能依据给定的工单 JSON 和处理规范回答。"
        "不要编造数量、状态或结论；输出中文 Markdown，包含：总体情况、重点问题、待跟进事项、建议。"
    )
    user = (
        f"用户要求：{request}\n\n"
        f"工单数据：\n{json.dumps(ticket_data, ensure_ascii=False)}\n\n"
        f"处理规范：\n{policy_context or '暂无相关规范'}"
    )
    response = httpx.post(
        f"{config['base_url'].rstrip('/')}/chat/completions",  # type: ignore[union-attr]
        headers={"Authorization": f"Bearer {config['api_key']}"},
        json={
            "model": config["model"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": 1000,
        },
        timeout=httpx.Timeout(connect=5, read=30, write=5, pool=5),
    )
    response.raise_for_status()
    return (response.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def _template_report(ticket_data: dict[str, Any]) -> str:
    """无模型时的本地模板，体现“事实统计”和“语言生成”可以解耦。"""
    summary = ticket_data["summary"]
    high = summary["high_priority"]
    unresolved = summary["unresolved"]
    lines = [
        f"## {ticket_data['date']} 工单简报",
        "",
        f"### 1. 总体情况\n昨日共收到 **{summary['total']}** 条工单。",
        f"- 状态分布：{_format_counts(summary['by_status'])}",
        f"- 优先级分布：{_format_counts(summary['by_priority'])}",
        f"- 部门分布：{_format_counts(summary['by_department'])}",
        "",
        "### 2. 重点问题",
    ]
    if high:
        lines.extend(f"- [{item['id']}] {item['title']}（{item['department']}，负责人：{item['assignee']}）" for item in high)
    else:
        lines.append("- 昨日没有高优先级工单。")
    lines.extend(["", "### 3. 待跟进事项"])
    if unresolved:
        lines.extend(f"- [{item['id']}] {item['title']}：当前状态为 {item['status']}，负责人：{item['assignee']}。" for item in unresolved)
    else:
        lines.append("- 昨日工单均已解决。")
    lines.extend([
        "",
        "### 4. 建议",
        "- 优先跟进高优先级和未解决工单，补充预计完成时间。",
        "- 对重复出现的问题建立知识库条目，减少服务台重复处理。",
        "",
        "> 当前未配置 LLM，以上内容由程序根据数据库事实生成；配置模型后可生成更自然的管理层摘要。",
    ])
    return "\n".join(lines)


def _format_counts(values: dict[str, int]) -> str:
    return "、".join(f"{key}: {value}" for key, value in values.items()) or "无"
