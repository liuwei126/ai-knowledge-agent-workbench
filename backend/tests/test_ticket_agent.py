"""工单 Agent 的最小单元测试。

运行：
    cd backend
    python -m unittest discover -s tests -v

测试中 patch 掉向量检索，避免单元测试因为下载 Embedding 模型而变慢；
真实项目还应补充 API 集成测试和一组固定的 RAG 评测集。
"""

import unittest
from unittest.mock import patch

from app import db
from app.ticket_agent import classify_intent, run_ticket_agent
from app.ticket_db import query_tickets, summarize_tickets, today_range
from app.ticket_tools import query_yesterday_tickets


class TicketAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 测试直接使用 Demo 数据库；运行前由应用启动或手动初始化表结构。
        db.init_db()
        from app.ticket_db import seed_tickets
        seed_tickets()

    def test_date_range_uses_left_closed_right_open_interval(self):
        day, start, end = today_range("2026-10-15")
        self.assertEqual(day, "2026-10-15")
        self.assertEqual(start, "2026-10-15T00:00:00+08:00")
        self.assertEqual(end, "2026-10-16T00:00:00+08:00")

    def test_query_and_summary(self):
        _, start, end = today_range("2026-10-15")
        tickets = query_tickets(start, end)
        summary = summarize_tickets(tickets)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(len(summary["high_priority"]), 2)
        self.assertEqual(len(summary["unresolved"]), 2)

    def test_tool_returns_auditable_structured_data(self):
        result = query_yesterday_tickets("2026-10-15")
        self.assertTrue(result["request_id"].startswith("tool-"))
        self.assertEqual(result["summary"]["total"], 5)

    def test_intent_router(self):
        self.assertEqual(classify_intent("请总结昨日工单"), "ticket_report")
        self.assertEqual(classify_intent("差旅报销需要什么材料"), "knowledge_qa")

    @patch("app.ticket_agent.hybrid_search", return_value=([], "keyword-only"))
    def test_agent_fallback_report_without_llm(self, _mock_search):
        result = run_ticket_agent("请总结昨日工单", "2026-10-15")
        self.assertEqual(result["intent"], "ticket_report")
        self.assertEqual(result["provider"], "fallback-template")
        self.assertIn("5", result["answer"])
        self.assertTrue(any(step["name"] == "查询工单工具" for step in result["trace"]))


if __name__ == "__main__":
    unittest.main()
