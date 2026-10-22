"""HTTP 请求/响应的数据结构。

Pydantic 模型把输入边界集中在路由之外：非法内容在进入业务逻辑前就会被拒绝。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    """问答请求：限制长度，避免误提交超大文本拖慢本地检索。"""

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        # Field 的 min_length 会先挡掉空字符串；这里处理只含空格/换行的情况。
        # 折叠空白可避免用户复制的多行问题造成检索词分散，同时保留词间分隔。
        value = " ".join(value.split())
        if not value:
            raise ValueError("问题不能为空")
        return value


class FeedbackRequest(BaseModel):
    """前端对回答的轻量反馈，rating 只接受界面约定的两种值。"""

    conversation_id: str | None = Field(default=None, max_length=100)
    message_id: str | None = Field(default=None, max_length=100)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=1000)


class TicketReportRequest(BaseModel):
    """工单报告请求：date 为空时默认查询当前日期的前一天。"""

    message: str = Field(default="请总结昨日工单并给出待跟进建议", max_length=1000)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class LeadAnalyzeRequest(BaseModel):
    """线索分级请求：lead_ids 为空时分析全部待处理线索。"""

    lead_ids: list[str] = Field(default_factory=list, max_length=50)
    max_items: int = Field(default=10, ge=1, le=20)


class UploadResponse(BaseModel):
    document: dict[str, Any]
    chunks: int


class TraceStep(BaseModel):
    name: str
    status: str
    detail: str
    provider: str = "local"
