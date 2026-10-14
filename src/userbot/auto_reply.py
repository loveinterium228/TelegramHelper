"""Авто-ответ оффлайн. Жёсткие правила: только входящие в ЛС от людей (не боты),
только если включено и владелец действительно оффлайн (или статус скрыт),
один ответ на контакт раз в COOLDOWN_MINUTES."""
from __future__ import annotations

import asyncio
import html
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.tl.custom import Message as TgMessage
from telethon.tl.types import (
    User as TgUser,
    UserStatusOffline,
    UserStatusOnline,
)

from src.config import settings as app_settings
from src.core.chat_service import load_chat, message_to_text
from src.core.notifier import notifier
from src.core.style_profile import style_profile_as_prompt_hint
from src.db.models import AutoReplyLog, User, utc_now
from src.db.repo import (
    add_auto_reply_log,
    get_contact,
    get_or_create_user,
    upsert_contact,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider


logger = logging.getLogger(__name__)


COOLDOWN_MINUTES = 30
CONTEXT_LIMIT = 8
FIRST_REPLY_NOTE = ""


AUTO_REPLY_SYSTEM_BASE = (
    "Ты — персональный цифровой представитель владельца в Telegram. Ты ведешь переписку от первого лица («я»).\n"
    "Твоя задача — общаться естественно, спокойно и слегка нейтрально, как реальный человек в Telegram.\n\n"
    "ПРИНЦИПЫ ОБЩЕНИЯ:\n"
    "1. ЗЕРКАЛЬ ВАЙБ, НО НЕ БОРЩИ: подстраивайся под уровень сленга и тон собеседника, но без перегибов. "
    "Держись спокойно и нейтрально, без фальшивого восторга, заискивания и гипер-вовлеченности. "
    "Если пишут на сленге — отвечай спокойно на той же волне. Если пишут строго — отвечай так же сдержанно.\n"
    "2. ПОДХВАТЫВАЙ ТЕМУ И КОНТЕКСТ: смело используй ключевые слова и формулировки собеседника "
    "(проекты, созвоны, игры, ссылки, задачи), чтобы диалог был естественным и связным.\n"
    "3. НИКАКОГО КОРПОРАТИВНОГО ИЛИ ПСИХОЛОГИЧЕСКОГО ДУШНИЛОВА: никогда не строй шаблонные фразы вида "
    "«слышу тебя», «понимаю твои чувства», «ты написал, что...». Будь живым, спокойным парнем.\n"
    "4. СТРОГИЙ ЗАПРЕТ НА СЛОВО «ЧЕРКАНИ» / «ЧЕРКНИ»: никогда не используй эти слова. "
    "Говори: «расскажи», «скинь», «напиши», «закинь».\n"
    "5. ЗАЩИТА ИНТЕРЕСОВ И ВРЕМЕНИ: не давай обещаний за владельца. "
    "По проектам и делам — проси суть текстом («расскажи в двух словах, освобожусь — чекну»). "
    "По деньгам, переводам и долгам — спокойный вежливый отказ («ща не помогу с этим»).\n"
    "6. ОФОРМЛЕНИЕ: строго со строчной буквы (даже первое слово). Без точки в конце сообщения. "
    "Длина: 1-2 коротких емких предложения.\n\n"
    "ПРИМЕРЫ ЖИВЫХ РЕАКЦИЙ:\n"
    "- «го созвон» ➔ «ща не могу говорить, если срочно — расскажи тут текстом»\n"
    "- «го в кс» ➔ «го, ток попозже немного, ща занят»\n"
    "- «заинтересовал проект, давай обсудим детали» ➔ «добро, расскажи в двух словах суть, освобожусь — чекну»\n"
    "- «скинешь сотку до завтра?» ➔ «ща с деньгами не помогу, сорян»\n"
    "- «куда пропал?» ➔ «да в делах, раскидываю задачи потихоньку»\n"
    "- «как дела вообще?» ➔ «да потихоньку, работаю. ты как?»\n"
    "- «ты тут? ответь срочно» ➔ «да, на связи. что случилось?»\n"
    "- «можешь глянуть файлик?» ➔ «закинь сюда, как освобожусь — гляну»"
)


def _clean_reply(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # Если модель выдала несколько строк/абзацев — берем первую суть
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = lines[0]
    for _ in range(2):
        for q in ('"', "'", "«", "»", "“", "”", "`"):
            if text.startswith(q) and text.endswith(q) and len(text) > 1:
                text = text[1:-1].strip()
        for pfx in ("я:", "ответ:", "отвечаю:", "бот:", "me:"):
            if text.lower().startswith(pfx):
                text = text[len(pfx):].strip()
    # Жесткий фильтр на запрещенные слова
    for bad, good in (
        ("черкани", "расскажи"),
        ("черкни", "расскажи"),
        ("Черкани", "Расскажи"),
        ("Черкни", "Расскажи"),
    ):
        text = text.replace(bad, good)
    if text:
        text = text[0].lower() + text[1:]
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1].rstrip()
    return text


async def _is_offline(client: TelegramClient) -> bool:
    # скрытый статус трактуем как оффлайн — это безопасный default
    try:
        me = await client.get_me()
        status = getattr(me, "status", None)
        logger.info("auto-reply: me.status is %s", type(status).__name__ if status else None)
        if isinstance(status, UserStatusOnline):
            return False
        if isinstance(status, UserStatusOffline):
            return True
        return True
    except Exception:
        logger.exception("get_me failed in _is_offline")
        return False


async def _is_first_reply(owner_id: int, peer_id: int) -> bool:
    async with get_session() as session:
        result = await session.execute(
            select(AutoReplyLog.id)
            .where(
                AutoReplyLog.user_id == owner_id,
                AutoReplyLog.peer_id == peer_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is None


async def _recently_replied(owner_id: int, peer_id: int, cooldown_min: int = 0) -> bool:
    if cooldown_min <= 0:
        return False
    threshold = utc_now() - timedelta(minutes=cooldown_min)
    async with get_session() as session:
        result = await session.execute(
            select(AutoReplyLog)
            .where(
                AutoReplyLog.user_id == owner_id,
                AutoReplyLog.peer_id == peer_id,
                AutoReplyLog.created_at >= threshold,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


async def _build_reply_text(
    owner_telegram_id: int,
    peer_id: int,
    sender_name: str,
    incoming_text: str,
) -> str | None:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        provider = await build_provider(session, owner)
        contact = await get_contact(session, owner, peer_id)
        heavy = owner.settings.use_heavy_model

    if provider is None:
        logger.warning("auto-reply: no LLM provider configured")
        return None

    # подгружаем контекст последних сообщений
    from src.userbot.manager import _MANAGER_SINGLETON  # локальный импорт

    client = _MANAGER_SINGLETON.get_client(owner_telegram_id) if _MANAGER_SINGLETON else None
    history_text = ""
    if client is not None:
        try:
            messages = await load_chat(client, owner_telegram_id, peer_id, limit=CONTEXT_LIMIT)
            history_text = "\n".join(message_to_text(m) for m in messages[-CONTEXT_LIMIT:])
        except Exception:
            logger.exception("auto-reply: load_chat failed")

    persona = (owner.settings.owner_persona or "").strip()
    system = AUTO_REPLY_SYSTEM_BASE
    if persona:
        system += f"\n\nИНДИВИДУАЛЬНЫЕ ИНСТРУКЦИИ И ИНТЕРЕСЫ ВЛАДЕЛЬЦА (СТРОГО СЛЕДУЙ ИМ):\n{persona}"
    if contact and contact.style_profile:
        style_hint = style_profile_as_prompt_hint(contact.style_profile)
        if style_hint:
            system += f"\n\nДОПОЛНИТЕЛЬНЫЙ СТИЛЬ С ЭТИМ КОНТАКТОМ:\n{style_hint}"

    now_str = datetime.now().strftime("%H:%M")
    user_prompt = (
        f"Текущее время: {now_str}.\n"
        f"Собеседник: {sender_name}.\n"
        f"История переписки:\n{history_text}\n\n"
        f"Новое входящее сообщение: {incoming_text}\n\n"
        "Ответь собеседнику от первого лица, спокойно и нейтрально, соблюдая стиль и инструкции владельца:"
    )
    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user_prompt),
            ],
            heavy=heavy,
        )
        return _clean_reply(raw)
    except Exception:
        logger.exception("auto-reply: LLM call failed")
        return None


async def _make_handler(client: TelegramClient, owner_telegram_id: int):
    """Возвращает event handler, замкнутый на owner_telegram_id."""

    async def handler(event: events.NewMessage.Event) -> None:
        try:
            msg: TgMessage = event.message
            if msg.out:
                return
            sender = await event.get_sender()
            if not isinstance(sender, TgUser) or sender.bot:
                return  # только ЛС от человеков
            if not event.is_private:
                return

            async with get_session() as session:
                owner: User = await get_or_create_user(session, owner_telegram_id)
                if not owner.settings.auto_reply_enabled:
                    return
                # игнорируем архив, если опция включена
                existing = await get_contact(session, owner, sender.id)
                if owner.settings.ignore_archived and existing is not None and existing.is_archived:
                    return
                # запомним контакт
                parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
                display = " ".join(p for p in parts if p).strip() or (sender.username or str(sender.id))
                await upsert_contact(
                    session,
                    owner,
                    peer_id=sender.id,
                    peer_kind="user",
                    is_bot=bool(getattr(sender, "bot", False)),
                    display_name=display,
                    username=getattr(sender, "username", None),
                    phone=getattr(sender, "phone", None),
                )

            is_offline = await _is_offline(client)
            always_reply = getattr(owner.settings, "auto_reply_always", False)
            if not is_offline and not always_reply:
                logger.info("auto-reply: owner is ONLINE and auto_reply_always=False, skipping for %s", sender.id)
                return

            cooldown = owner.settings.auto_reply_cooldown_min
            if await _recently_replied(owner.id, sender.id, cooldown):
                logger.info("auto-reply: cooldown active (%d min) for %s, skipping", cooldown, sender.id)
                return

            incoming_text = msg.text or msg.message or ""
            if not incoming_text.strip():
                return  # медиа без текста — не отвечаем автоматически

            logger.info("auto-reply: preparing response for %s: %r", display, incoming_text[:80])

            # перечитаем настройки для режима/текста
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                mode = owner.settings.auto_reply_mode
                static_text = owner.settings.auto_reply_text or ""

            if mode == "smart":
                reply = await _build_reply_text(
                    owner_telegram_id, sender.id, display, incoming_text
                )
                if not reply:
                    logger.warning("auto-reply: empty or failed LLM response for %s", sender.id)
                    return
            else:  # static (default)
                reply = _clean_reply(static_text)
                if not reply:
                    return

            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                is_first = await _is_first_reply(owner.id, sender.id)
            if is_first and FIRST_REPLY_NOTE:
                reply = f"{reply}\n\n{FIRST_REPLY_NOTE}"

            # Реалистичная человеческая пауза (чтение + набор)
            read_delay = random.uniform(1.8, 3.5)
            await asyncio.sleep(read_delay)

            try:
                await client.send_read_acknowledge(event.chat_id, max_id=msg.id)
            except Exception:
                pass

            typing_delay = min(5.0, max(1.8, len(reply) * 0.08 + random.uniform(0.5, 1.2)))
            try:
                async with client.action(event.chat_id, "typing"):
                    await asyncio.sleep(typing_delay)
            except Exception:
                await asyncio.sleep(typing_delay)

            await event.respond(reply)
            logger.info("auto-reply: sent reply to %s: %r", display, reply[:80])

            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                await add_auto_reply_log(
                    session,
                    user_id=owner.id,
                    peer_id=sender.id,
                    peer_name=display,
                    incoming_text=incoming_text[:500],
                    reply_text=reply,
                )

            disp_esc = html.escape(display)
            in_esc = html.escape(incoming_text[:200])
            rep_esc = html.escape(reply)
            await notifier.notify(
                f"🤖 <b>Авто-ответ</b> для <b>{disp_esc}</b>\n\n"
                f"<i>Им:</i> {in_esc}\n"
                f"<i>Я:</i> {rep_esc}"
            )
        except Exception:
            logger.exception("auto-reply handler failed")

    return handler


def attach_auto_reply(client: TelegramClient, owner_telegram_id: int) -> None:

    async def _wrapper(event):
        h = await _make_handler(client, owner_telegram_id)
        await h(event)

    client.add_event_handler(_wrapper, events.NewMessage(incoming=True))
    logger.info("Auto-reply handler attached for user %s", owner_telegram_id)
