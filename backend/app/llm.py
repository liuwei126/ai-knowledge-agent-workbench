"""LLM 回答模块：OpenAI-compatible Chat Completions 客户端 + 本地模板兜底。

配置来源 backend/.env（不提交 Git）：
    OPENAI_BASE_URL=https://api.example.com/v1
    OPENAI_API_KEY=sk-xxx
    OPENAI_MODEL=gpt-4o-mini
三者任一缺失或调用失败时，自动降级为本地模板回答，系统开箱即用。
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# 简易 .env 解析：KEY=VALUE 逐行，支持注释与两侧引号，避免引入额外依赖。
def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def get_llm_config() -> dict[str, str | None]:
    """返回当前 LLM 配置；base_url/key/model 齐全才算可用。"""
    env = _load_env()
    # 环境变量优先于 .env 文件，便于部署时注入。
    base_url = os.environ.get("OPENAI_BASE_URL") or env.get("OPENAI_BASE_URL")
    api_key = os.environ.get("OPENAI_API_KEY") or env.get("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_MODEL") or env.get("OPENAI_MODEL")
    configured = bool(base_url and api_key and model)
    return {
        "base_url": base_url if configured else None,
        "api_key": api_key if configured else None,
        "model": model if configured else None,
        "configured": configured,
    }

SYSTEM_PROMPT = """你是企业内部知识库助手。请严格依据提供的【参考资料】回答问题：
1. 只使用参考资料中的信息，不要编造制度条款、数字或流程；
2. 回答末尾用「依据：」列出所引用的资料标题；
3. 如果参考资料不足以回答问题，直接说明知识库中暂无相关依据，并建议用户补充文档；
4. 用简洁的中文回答，条理清晰。"""


def generate_answer(question: str, context: str, citations: list[dict]) -> tuple[str, str]:
    """生成回答，返回 (回答文本, provider)。

    provider 取值：openai-compatible（LLM 生成）/ fallback-template（本地模板）。
    LLM 输出不改变 citations 结构——引用始终来自检索命中，模型只在给定资料内组织语言。
    """
    config = get_llm_config()
    if config["configured"] and context:
        try:
            answer = _call_llm(config, question, context)
            if answer:
                return answer, "openai-compatible"
        except Exception as exc:
            logger.warning("LLM 调用失败，降级为模板回答：%s", exc)
    return _template_answer(question, context), "fallback-template"


def _call_llm(config: dict[str, str | None], question: str, context: str) -> str:
    """调用 OpenAI-compatible /chat/completions；失败抛异常由上层降级。"""
    import httpx

    response = httpx.post(
        f"{config['base_url'].rstrip('/')}/chat/completions",  # type: ignore[union-attr]
        headers={"Authorization": f"Bearer {config['api_key']}"},
        json={
            "model": config["model"],
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"【参考资料】\n{context}\n\n【问题】\n{question}"},
            ],
            "temperature": 0.2,
            "max_tokens": 800,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()


def _template_answer(question: str, context: str) -> str:
    """本地模板回答：检索到依据时拼接要点，无依据时安全拒答。"""
    if not context:
        return (
            "当前知识库没有检索到足够依据回答这个问题，因此我不能编造答案。"
            "请换一个说法重试，或先上传相关制度文档。"
        )
    return (
        "根据知识库检索结果：\n\n"
        f"{context}\n\n"
        "（当前为本地模板回答；在 backend/.env 配置 OPENAI_BASE_URL / OPENAI_API_KEY / "
        "OPENAI_MODEL 后可启用真实 LLM 生成。）"
    )
