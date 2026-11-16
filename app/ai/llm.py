"""LLM 공급자 선택 (Gemini / OpenAI / 규칙 기반 fallback)."""

import logging
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger("arithon.ai")


def provider_name() -> str:
    s = get_settings()
    choice = (s.ai_provider or "auto").lower()
    gemini_key = s.gemini_api_key or s.google_api_key
    if choice == "auto":
        if gemini_key:
            return "gemini"
        if s.openai_api_key:
            return "openai"
        return "fallback"
    if choice == "gemini" and not gemini_key:
        logger.warning("AI_PROVIDER=gemini 이지만 GEMINI_API_KEY 가 없어 fallback 을 사용합니다.")
        return "fallback"
    if choice == "openai" and not s.openai_api_key:
        logger.warning("AI_PROVIDER=openai 이지만 OPENAI_API_KEY 가 없어 fallback 을 사용합니다.")
        return "fallback"
    return choice if choice in ("gemini", "openai", "fallback") else "fallback"


@lru_cache
def chat_model():
    """LangChain 채팅 모델. fallback 이면 None."""
    s = get_settings()
    name = provider_name()
    if name == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=s.gemini_model,
            google_api_key=s.gemini_api_key or s.google_api_key,
            temperature=0.4,
            timeout=s.ai_timeout_seconds,
            max_retries=1,
        )
    if name == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.openai_model,
            api_key=s.openai_api_key,
            temperature=0.4,
            timeout=s.ai_timeout_seconds,
            max_retries=1,
        )
    return None
