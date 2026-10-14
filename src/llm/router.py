from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User
from src.db.repo import get_api_key
from src.llm.base import LLMProvider
from src.llm.gemini_provider import GeminiProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider


async def build_provider(session: AsyncSession, user: User) -> LLMProvider | None:
    """Создаёт провайдера согласно настройкам пользователя. None — если ключ не задан."""
    provider_name = user.settings.llm_provider if user.settings else "groq"
    key = await get_api_key(session, user, provider_name)
    if not key:
        # Fallback: если для выбранного провайдера ключа нет, пробуем другие
        for fallback in ("groq", "openai", "gemini"):
            if fallback != provider_name:
                alt_key = await get_api_key(session, user, fallback)
                if alt_key:
                    provider_name = fallback
                    key = alt_key
                    break
    if not key:
        return None
    if provider_name == "groq":
        return GroqProvider(key)
    if provider_name == "openai":
        return OpenAIProvider(key)
    if provider_name == "gemini":
        return GeminiProvider(key)
    return None
