"""Промпты для саммари, черновика ответа и «где мы остановились»."""

from src.core.chat_service import message_to_text
from src.core.style_profile import style_profile_as_prompt_hint
from src.core.text_sanitizer import sanitize_html
from src.db.models import Contact, Message
from src.llm.base import ChatMessage, LLMProvider


SUMMARY_SYSTEM = (
    "You are an executive assistant preparing a concise chat summary.\n"
    "Respond in the same language as the conversation (English or Russian).\n"
    "Output structure (Telegram HTML formatting):\n"
    "📝 <b>Key Takeaways / Главное</b> — 2–4 concise bullets.\n"
    "🎯 <b>Pending Questions & Tasks / Задачи</b> — what is expected of me.\n"
    "📅 <b>Agreements & Deadlines / Договорённости</b> — dates, meetings, commitments.\n"
    "🌡 <b>Tone / Тон</b> — one short phrase.\n"
    "Use Telegram HTML tags (<b>, <i>, <code>). Do NOT use markdown."
)


DRAFT_SYSTEM = (
    "You are drafting a contextual reply on my behalf. Write in the same language and style as the chat.\n"
    "Output ONLY the reply text, without preambles, meta-commentary, or quotes.\n"
    "Account for recent messages and avoid repeating already stated facts.\n"
    "If critical context is ambiguous, ask a short clarifying question instead of making assumptions."
)


CATCHUP_SYSTEM = (
    "I have been away from this chat. Catch me up concisely:\n"
    "Respond in the same language as the conversation (English or Russian).\n"
    "1) <b>Where we left off / Где остановились</b> — 2–3 bullets on current status.\n"
    "2) <b>What is expected of me / Чего ждут</b> — required actions or answers.\n"
    "3) <b>Draft Reply / Черновик ответа</b> — 1–3 sentences matching my communication style.\n"
    "Use Telegram HTML tags (<b>, <i>, <code>). Do NOT use markdown."
)


async def summarize_chat(
    provider: LLMProvider,
    contact: Contact,
    messages: list[Message],
    *,
    heavy: bool = False,
) -> str:
    transcript = "\n".join(message_to_text(m) for m in messages)
    user_prompt = (
        f"Собеседник: {contact.display_name}\n\n"
        f"Переписка (последние {len(messages)} сообщений):\n{transcript}"
    )
    raw = await provider.chat(
        [
            ChatMessage(role="system", content=SUMMARY_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ],
        heavy=heavy,
    )
    return sanitize_html(raw)


async def draft_reply(
    provider: LLMProvider,
    contact: Contact,
    messages: list[Message],
    *,
    instruction: str | None = None,
    heavy: bool = False,
) -> str:
    transcript = "\n".join(message_to_text(m) for m in messages)
    style_hint = style_profile_as_prompt_hint(contact.style_profile)
    system = DRAFT_SYSTEM
    if style_hint:
        system = system + "\n" + style_hint
    user_prompt = (
        f"Собеседник: {contact.display_name}\n\n"
        f"Контекст переписки:\n{transcript}\n\n"
        + (f"Инструкция: {instruction}" if instruction else "Напиши уместный ответ на последнее сообщение.")
    )
    raw = await provider.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user_prompt),
        ],
        heavy=heavy,
    )
    return sanitize_html(raw)


async def catchup(
    provider: LLMProvider,
    contact: Contact,
    messages: list[Message],
    *,
    heavy: bool = False,
) -> str:
    transcript = "\n".join(message_to_text(m) for m in messages)
    style_hint = style_profile_as_prompt_hint(contact.style_profile)
    system = CATCHUP_SYSTEM
    if style_hint:
        system = system + "\n" + style_hint
    user_prompt = (
        f"Собеседник: {contact.display_name}\n\nПоследние сообщения:\n{transcript}"
    )
    raw = await provider.chat(
        [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user_prompt),
        ],
        heavy=heavy,
    )
    return sanitize_html(raw)
