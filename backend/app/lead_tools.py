"""线索工具层：对外部信息源的访问封装（当前为 mock，预留真实搜索替换点）。

真实项目里 enrich_company 会调用联网搜索（Tavily/SerpAPI）或企业信息
API（企查查/天眼查）；本 Demo 用内置知识模拟返回，但完整保留了
审计、耗时统计和"查不到"的语义——上层编排不需要知道背后是真是假。
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from .audit import audit_tool_call

logger = logging.getLogger(__name__)

# mock 知识库：模拟"公开信息里能查到的补充画像"。
# key 会被 company 名包含匹配；value 是该公司的公开补充信息。
_MOCK_WEB_FACTS: dict[str, dict[str, Any]] = {
    "智造科技": {"recent_news": "近一年获 B 轮融资，主推设备后市场 SaaS", "num_branches": 3},
    "华东冷链": {"recent_news": "新建华东二号冷链枢纽仓", "num_branches": 40},
    "康宁医疗": {"recent_news": "通过 ISO 13485 复审", "num_branches": 2},
    "绿源新能源": {"recent_news": "三期基地在建，排产系统招标中", "num_branches": 4},
    "蓝海跨境": {"recent_news": "计划扩容海外仓至 12 个", "num_branches": 8},
    "极速出行": {"recent_news": "司机端 App 完成 5.0 改版", "num_branches": 30},
}


def enrich_company(company: str) -> dict[str, Any]:
    """工具：按公司名收集公开补充信息。

    返回结构约定：
    - found=True 时 extra 为补充字段，sources 标注信息来源；
    - found=False 表示公开渠道查不到，不是错误——上层据此标记
      info_incomplete，而不是编造数据继续打分。
    """
    request_id = f"lead-tool-{uuid4().hex[:10]}"
    tool_name = "enrich_company"
    started = time.monotonic()
    try:
        extra = _search_mock(company)
        output = {
            "found": extra is not None,
            "extra": extra,
            "sources": ["mock-web-search"] if extra is not None else [],
        }
        audit_tool_call(
            request_id, tool_name, {"company": company}, output, "success",
            int((time.monotonic() - started) * 1000),
        )
        logger.info("tool_success request_id=%s tool=%s found=%s", request_id, tool_name, output["found"])
        return {"request_id": request_id, **output}
    except Exception as exc:
        audit_tool_call(
            request_id, tool_name, {"company": company}, None, "failed",
            int((time.monotonic() - started) * 1000), str(exc),
        )
        logger.exception("tool_failed request_id=%s tool=%s", request_id, tool_name)
        raise


def _search_mock(company: str) -> dict[str, Any] | None:
    """内置 mock 搜索：包含匹配即命中。接入真实搜索时只需替换本函数。"""
    for key, facts in _MOCK_WEB_FACTS.items():
        if key in company or company in key:
            return dict(facts)
    return None
