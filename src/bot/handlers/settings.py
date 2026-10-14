"""/settings — главное меню и разделы. callback_data: set:sec / set:tog / set:choose / set:input."""
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.bot.states import SettingsStates
from src.config import LLMDefaults
from src.core.timeutil import TZ_PRESETS, is_valid_tz, tz_short
from src.db.repo import get_api_key, get_or_create_user, upsert_api_key
from src.db.session import get_session
from src.llm.gemini_provider import GeminiProvider
from src.llm.groq_provider import GroqProvider
from src.llm.openai_provider import OpenAIProvider


router = Router(name="settings")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


HM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _check(value: bool) -> str:
    return "✅" if value else "❌"


# ---------- Главное меню ----------

async def _render_menu(telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings
        openai_key = await get_api_key(session, owner, "openai")
        gemini_key = await get_api_key(session, owner, "gemini")
        groq_key = await get_api_key(session, owner, "groq")

    text = (
        "⚙ <b>Настройки</b>\n\n"
        f"🌍 Часовой пояс: <b>{tz_short(s.timezone)}</b>\n"
        f"🔄 Авто-ответ: {_check(s.auto_reply_enabled)} (кулдаун {s.auto_reply_cooldown_min}м)\n"
        f"☀ Дайджест: {_check(s.digest_enabled)} ({s.digest_time})\n"
        f"⏰ Напоминания: {_check(s.reminders_enabled)} (за {s.reminder_lead_hours}ч; просрочки {_check(s.reminder_overdue_enabled)})\n"
        f"📰 Новости: {_check(s.news_enabled)} (окно {s.news_window_hours}ч)\n"
        f"🛡 Игнорировать архив: {_check(s.ignore_archived)}\n"
        f"🤖 LLM: <b>{s.llm_provider}</b> · {'тяжёлая' if s.use_heavy_model else 'лёгкая'}\n"
        f"🎤 Транскрипция: <b>{s.transcription_mode}</b>\n"
        f"🔑 Ключи: Groq {_check(bool(groq_key))} · OpenAI {_check(bool(openai_key))} · Gemini {_check(bool(gemini_key))}\n\n"
        "<i>Тапни раздел, чтобы открыть его настройки и описание.</i>"
    )
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🌍 Часовой пояс", callback_data="set:sec:tz"),
        InlineKeyboardButton(text="🔄 Авто-ответ", callback_data="set:sec:auto_reply"),
    )
    kb.row(
        InlineKeyboardButton(text="☀ Дайджест", callback_data="set:sec:digest"),
        InlineKeyboardButton(text="⏰ Напоминания", callback_data="set:sec:reminders"),
    )
    kb.row(
        InlineKeyboardButton(text="📰 Новости", callback_data="set:sec:news"),
        InlineKeyboardButton(text="🛡 Приватность", callback_data="set:sec:privacy"),
    )
    kb.row(
        InlineKeyboardButton(text="🤖 LLM", callback_data="set:sec:llm"),
        InlineKeyboardButton(text="🎤 Транскрипция", callback_data="set:sec:transcription"),
    )
    kb.row(InlineKeyboardButton(text="🔑 API-ключи", callback_data="set:sec:keys"))
    kb.row(InlineKeyboardButton(text="❌ Закрыть", callback_data="set:close"))
    return text, kb.as_markup()


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    text, kb = await _render_menu(message.from_user.id)
    await message.answer(text, reply_markup=kb)


async def _safe_edit(message, text: str, kb) -> None:
    # глушит безобидное "message is not modified" при повторном тапе той же опции
    if message is None:
        return
    try:
        await message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            raise


@router.callback_query(F.data == "set:menu")
async def cb_menu(callback: CallbackQuery) -> None:
    text, kb = await _render_menu(callback.from_user.id)
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


@router.callback_query(F.data == "set:close")
async def cb_close(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.delete()
    await callback.answer()


# ---------- Универсальные ручки тогглов и выбора ----------

BOOL_KEYS = {
    "auto_reply_enabled",
    "auto_reply_always",
    "ignore_archived",
    "digest_enabled",
    "reminders_enabled",
    "reminder_overdue_enabled",
    "news_enabled",
    "use_heavy_model",
}

CHOICE_KEYS = {
    "llm_provider": {"groq", "openai", "gemini"},
    "transcription_mode": {"local", "api", "hybrid"},
    "auto_reply_mode": {"static", "smart"},
}

NUMERIC_KEYS = {
    "auto_reply_cooldown_min",
    "reminder_lead_hours",
    "news_window_hours",
}


@router.callback_query(F.data.startswith("set:tog:"))
async def cb_toggle(callback: CallbackQuery) -> None:
    key = callback.data.split(":", 2)[2]
    if key not in BOOL_KEYS:
        await callback.answer("Неизвестный переключатель", show_alert=True)
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        current = getattr(owner.settings, key)
        setattr(owner.settings, key, not current)
    await callback.answer("Готово")
    await _refresh_section(callback, _section_for_key(key))


@router.callback_query(F.data.startswith("set:choose:"))
async def cb_choose(callback: CallbackQuery) -> None:
    _, _, key, value = callback.data.split(":", 3)
    if key in CHOICE_KEYS:
        if value not in CHOICE_KEYS[key]:
            await callback.answer("Невалидное значение", show_alert=True)
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            setattr(owner.settings, key, value)
    elif key in NUMERIC_KEYS:
        try:
            ivalue = max(0, int(value))
        except ValueError:
            await callback.answer("Невалидное число", show_alert=True)
            return
        async with get_session() as session:
            owner = await get_or_create_user(session, callback.from_user.id)
            setattr(owner.settings, key, ivalue)
    else:
        await callback.answer("Неизвестное поле", show_alert=True)
        return
    await callback.answer("Готово")
    await _refresh_section(callback, _section_for_key(key))


def _section_for_key(key: str) -> str:
    return {
        "auto_reply_enabled": "auto_reply",
        "auto_reply_always": "auto_reply",
        "auto_reply_cooldown_min": "auto_reply",
        "auto_reply_mode": "auto_reply",
        "auto_reply_text": "auto_reply",
        "ignore_archived": "privacy",
        "digest_enabled": "digest",
        "reminders_enabled": "reminders",
        "reminder_lead_hours": "reminders",
        "reminder_overdue_enabled": "reminders",
        "news_enabled": "news",
        "news_window_hours": "news",
        "llm_provider": "llm",
        "use_heavy_model": "llm",
        "transcription_mode": "transcription",
    }.get(key, "menu")


async def _refresh_section(callback: CallbackQuery, section: str) -> None:
    if section == "menu":
        text, kb = await _render_menu(callback.from_user.id)
    else:
        text, kb = await _render_section(callback.from_user.id, section)
    await _safe_edit(callback.message, text, kb)


# ---------- Разделы ----------

@router.callback_query(F.data.startswith("set:sec:"))
async def cb_open_section(callback: CallbackQuery) -> None:
    section = callback.data.split(":", 2)[2]
    text, kb = await _render_section(callback.from_user.id, section)
    await _safe_edit(callback.message, text, kb)
    await callback.answer()


def _back_row():
    return [InlineKeyboardButton(text="← Меню настроек", callback_data="set:menu")]


async def _render_section(telegram_id: int, section: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        s = owner.settings
        openai_key = await get_api_key(session, owner, "openai")
        gemini_key = await get_api_key(session, owner, "gemini")
        groq_key = await get_api_key(session, owner, "groq")

    kb = InlineKeyboardBuilder()

    if section == "auto_reply":
        mode_label = "🤖 умный (LLM в твоём стиле)" if s.auto_reply_mode == "smart" else "📝 заготовленный текст"
        snippet = (s.auto_reply_text or "").strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:77] + "…"
        persona_snippet = (s.owner_persona or "Не заданы (базовый живой стиль)").strip().replace("\n", " ")
        if len(persona_snippet) > 80:
            persona_snippet = persona_snippet[:77] + "…"
        cooldown_str = "0 мин (на каждое)" if s.auto_reply_cooldown_min == 0 else f"{s.auto_reply_cooldown_min} мин"
        text = (
            "🔄 <b>Авто-ответ</b>\n\n"
            "Когда я <b>оффлайн</b> (или включено «даже онлайн») и приходит личное сообщение — бот отправляет ответ.\n"
            "Только ЛС, не группы и не боты.\n\n"
            "<b>Режимы</b>:\n"
            "• <b>заготовленный</b> — отправляется один и тот же текст.\n"
            "• <b>умный</b> — LLM пишет ответ в твоём стиле, представляет твои интересы и учитывает контекст.\n\n"
            f"Статус: <b>{'ВКЛ' if s.auto_reply_enabled else 'ВЫКЛ'}</b>\n"
            f"Режим: <b>{mode_label}</b>\n"
            f"Кулдаун: <b>{cooldown_str}</b>\n"
            f"Отвечать даже онлайн: <b>{_check(s.auto_reply_always)}</b>\n\n"
            f"🧠 <b>Твои инструкции</b>:\n<i>«{persona_snippet}»</i>\n\n"
            f"📝 <b>Текст заготовки</b>:\n<i>«{snippet}»</i>"
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.auto_reply_enabled)} Включить авто-ответ",
            callback_data="set:tog:auto_reply_enabled",
        ))
        kb.row(
            InlineKeyboardButton(
                text=("• " if s.auto_reply_mode == "static" else "") + "📝 Заготовка",
                callback_data="set:choose:auto_reply_mode:static",
            ),
            InlineKeyboardButton(
                text=("• " if s.auto_reply_mode == "smart" else "") + "🤖 Умный",
                callback_data="set:choose:auto_reply_mode:smart",
            ),
        )
        kb.row(InlineKeyboardButton(
            text="🧠 Задать инструкции / О себе",
            callback_data="set:input:owner_persona",
        ))
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.auto_reply_always)} Отвечать даже когда онлайн",
            callback_data="set:tog:auto_reply_always",
        ))
        kb.row(InlineKeyboardButton(
            text="✏ Изменить текст заготовки",
            callback_data="set:input:auto_reply_text",
        ))
        kb.row(*[
            InlineKeyboardButton(
                text=("• " if s.auto_reply_cooldown_min == m else "") + (f"{m}м" if m > 0 else "0м"),
                callback_data=f"set:choose:auto_reply_cooldown_min:{m}",
            ) for m in (0, 5, 15, 30, 60)
        ])
        kb.row(*_back_row())

    elif section == "digest":
        text = (
            "☀ <b>Утренний дайджест</b>\n\n"
            "Раз в сутки в указанное время получаю сводку: что произошло за ночь, кто ждёт ответа, "
            "горящие обещания и сколько было авто-ответов.\n\n"
            f"Статус: <b>{'ВКЛ' if s.digest_enabled else 'ВЫКЛ'}</b>\n"
            f"Время: <b>{s.digest_time}</b> · {tz_short(s.timezone)}\n\n"
            "Часовой пояс — отдельный раздел в /settings.\n"
            "Для разовой сводки — команда /digest"
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.digest_enabled)} Включить дайджест",
            callback_data="set:tog:digest_enabled",
        ))
        kb.row(InlineKeyboardButton(text=f"⏰ Время: {s.digest_time}", callback_data="set:input:digest_time"))
        kb.row(*_back_row())

    elif section == "reminders":
        text = (
            "⏰ <b>Напоминания о дедлайнах</b>\n\n"
            "Бот подгружает обещания из переписок (см. /todos и кнопку «Задачи» в /chat) и пинает, "
            "когда дедлайн близок или просрочен.\n\n"
            f"Статус: <b>{'ВКЛ' if s.reminders_enabled else 'ВЫКЛ'}</b>\n"
            f"Заранее за: <b>{s.reminder_lead_hours} ч</b>\n"
            f"Алерт о просрочках: <b>{'ВКЛ' if s.reminder_overdue_enabled else 'ВЫКЛ'}</b>"
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.reminders_enabled)} Включить напоминания",
            callback_data="set:tog:reminders_enabled",
        ))
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.reminder_overdue_enabled)} Алерт при просрочке",
            callback_data="set:tog:reminder_overdue_enabled",
        ))
        kb.row(*[
            InlineKeyboardButton(
                text=("• " if s.reminder_lead_hours == h else "") + f"{h}ч",
                callback_data=f"set:choose:reminder_lead_hours:{h}",
            ) for h in (1, 2, 4, 12, 24)
        ])
        kb.row(*_back_row())

    elif section == "news":
        text = (
            "📰 <b>Новости</b>\n\n"
            "Команда <code>/news тема</code> ищет посты в твоих подписанных каналах за последние N часов и "
            "собирает структурированный обзор.\n\n"
            "<b>Авто-новости</b> (этот тогглер): если включено, каждое утро в указанное время бот шлёт "
            "дайджест по каждой теме из <b>/news_topics</b>.\n\n"
            "Чтобы ограничить выборку конкретными каналами — /news_channels.\n\n"
            f"Авто-новости: <b>{'ВКЛ' if s.news_enabled else 'ВЫКЛ'}</b>\n"
            f"Время отправки: <b>{s.news_digest_time}</b> · {tz_short(s.timezone)}\n"
            f"Окно по умолчанию: <b>{s.news_window_hours} ч</b>"
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.news_enabled)} Включить авто-новости",
            callback_data="set:tog:news_enabled",
        ))
        kb.row(InlineKeyboardButton(
            text=f"⏰ Время: {s.news_digest_time}",
            callback_data="set:input:news_time",
        ))
        kb.row(*[
            InlineKeyboardButton(
                text=("• " if s.news_window_hours == h else "") + f"{h}ч",
                callback_data=f"set:choose:news_window_hours:{h}",
            ) for h in (6, 12, 24, 48, 72)
        ])
        kb.row(InlineKeyboardButton(text="📋 Темы → /news_topics", callback_data="set:noop:news_topics"))
        kb.row(*_back_row())

    elif section == "llm":
        if s.llm_provider == "groq":
            active = LLMDefaults.GROQ_CHAT_HEAVY if s.use_heavy_model else LLMDefaults.GROQ_CHAT_LIGHT
        elif s.llm_provider == "openai":
            active = LLMDefaults.OPENAI_CHAT_HEAVY if s.use_heavy_model else LLMDefaults.OPENAI_CHAT_LIGHT
        else:
            active = LLMDefaults.GEMINI_CHAT_HEAVY if s.use_heavy_model else LLMDefaults.GEMINI_CHAT_LIGHT
        text = (
            "🤖 <b>LLM-провайдер</b>\n\n"
            "Кто отвечает на запросы и пишет черновики/саммари. Лёгкая модель — для рутины, "
            "тяжёлая — для длинных переписок и сложного анализа.\n\n"
            f"Провайдер: <b>{s.llm_provider}</b>\n"
            f"Режим: <b>{'тяжёлая' if s.use_heavy_model else 'лёгкая'}</b>\n"
            f"Активная модель: <code>{active}</code>"
        )
        kb.row(
            InlineKeyboardButton(
                text=("• " if s.llm_provider == "groq" else "") + "⚡ Groq (Free)",
                callback_data="set:choose:llm_provider:groq",
            ),
            InlineKeyboardButton(
                text=("• " if s.llm_provider == "openai" else "") + "OpenAI",
                callback_data="set:choose:llm_provider:openai",
            ),
            InlineKeyboardButton(
                text=("• " if s.llm_provider == "gemini" else "") + "Gemini",
                callback_data="set:choose:llm_provider:gemini",
            ),
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.use_heavy_model)} Тяжёлая модель",
            callback_data="set:tog:use_heavy_model",
        ))
        kb.row(*_back_row())

    elif section == "transcription":
        text = (
            "🎤 <b>Транскрипция голосовых и аудио</b>\n\n"
            "<b>local</b> — faster-whisper на твоей машине (бесплатно, приватно, нужны ресурсы).\n"
            "<b>api</b> — OpenAI Whisper (~$0.006/мин, нужен OpenAI key, аудио уходит в OpenAI).\n"
            "<b>hybrid</b> — local, fallback в API при ошибке.\n\n"
            f"Текущий: <b>{s.transcription_mode}</b>"
        )
        for mode in ("local", "api", "hybrid"):
            kb.row(InlineKeyboardButton(
                text=("• " if s.transcription_mode == mode else "") + mode,
                callback_data=f"set:choose:transcription_mode:{mode}",
            ))
        kb.row(*_back_row())

    elif section == "tz":
        text = (
            "🌍 <b>Часовой пояс</b>\n\n"
            "От него отталкиваются:\n"
            "• время утреннего дайджеста и авто-новостей\n"
            "• отображение дедлайнов в /todos и напоминаниях\n"
            "• временные метки в дайджестах\n\n"
            f"Сейчас: <b>{tz_short(s.timezone)}</b>\n\n"
            "Тапни пресет ниже или введи свой IANA-таймзону кнопкой «Другой…»."
        )
        # пресеты по 2 в ряд
        for i in range(0, len(TZ_PRESETS), 2):
            buttons = []
            for tz in TZ_PRESETS[i:i + 2]:
                mark = "• " if s.timezone == tz else ""
                buttons.append(InlineKeyboardButton(text=mark + tz, callback_data=f"set:tz:{tz}"))
            kb.row(*buttons)
        kb.row(InlineKeyboardButton(text="✏ Другой…", callback_data="set:input:timezone"))
        kb.row(*_back_row())

    elif section == "privacy":
        text = (
            "🛡 <b>Приватность и видимость</b>\n\n"
            "Что бот <b>смотрит и обрабатывает</b> по умолчанию.\n\n"
            "<b>Игнорировать архив</b> — чаты в архиве Telegram не подгружаются ни в /chat, "
            "ни в /search, ни в /news, ни в авто-ответ. Включено по умолчанию.\n\n"
            f"Игнорировать архив: <b>{'ВКЛ' if s.ignore_archived else 'ВЫКЛ'}</b>\n\n"
            "<i>Изменения вступают в силу для следующих запросов. Архивный статус подтягивается "
            "при /sync.</i>"
        )
        kb.row(InlineKeyboardButton(
            text=f"{_check(s.ignore_archived)} Игнорировать архив",
            callback_data="set:tog:ignore_archived",
        ))
        kb.row(*_back_row())

    elif section == "keys":
        text = (
            "🔑 <b>API-ключи</b>\n\n"
            "Хранятся зашифрованными (Fernet). Можно перезаписать в любой момент.\n\n"
            f"⚡ Groq (Free): {_check(bool(groq_key))}\n"
            f"OpenAI: {_check(bool(openai_key))}\n"
            f"Gemini: {_check(bool(gemini_key))}"
        )
        kb.row(
            InlineKeyboardButton(text="🔑 Groq key", callback_data="set:input:groq_key"),
        )
        kb.row(
            InlineKeyboardButton(text="🔑 OpenAI key", callback_data="set:input:openai_key"),
            InlineKeyboardButton(text="🔑 Gemini key", callback_data="set:input:gemini_key"),
        )
        kb.row(*_back_row())

    else:
        text = "Раздел не найден."
        kb.row(*_back_row())

    return text, kb.as_markup()


# ---------- FSM-вводы ----------

@router.callback_query(F.data == "set:input:groq_key")
async def cb_input_groq(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_groq_key)
    await callback.message.answer(
        "Пришли Groq API key с <code>console.groq.com</code> (начинается с <code>gsk_</code>). "
        "Проверю и сохраню. /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:input:openai_key")
async def cb_input_openai(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_openai_key)
    await callback.message.answer(
        "Пришли OpenAI API key (начинается с <code>sk-</code>). Проверю и сохраню. /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:input:gemini_key")
async def cb_input_gemini(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_gemini_key)
    await callback.message.answer(
        "Пришли Gemini API key с <code>aistudio.google.com</code>. Проверю и сохраню. /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:input:digest_time")
async def cb_input_digest(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_digest_time)
    await callback.message.answer("Введи время в формате <code>HH:MM</code> (UTC). /cancel — отмена.")
    await callback.answer()


@router.callback_query(F.data == "set:input:auto_reply_text")
async def cb_input_auto_reply(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_auto_reply_text)
    await callback.message.answer(
        "Пришли новый текст автоответа. Будет отправляться, когда ты оффлайн "
        "(в режиме «заготовка»). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:input:owner_persona")
async def cb_input_owner_persona(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_owner_persona)
    await callback.message.answer(
        "🧠 <b>Инструкции для автоответа</b>\n\n"
        "Опиши, кто ты, чем занимаешься, как общаться с людьми, что отвечать по работе/сделкам/услугам, "
        "как общаться с друзьями и какие твои интересы.\n\n"
        "<i>Бот будет строго следовать этим правилам при каждом ответе.</i>\n\n"
        "/cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:input:news_time")
async def cb_input_news_time(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_news_time)
    await callback.message.answer(
        "Введи время утренних авто-новостей в <code>HH:MM</code> (UTC). /cancel — отмена."
    )
    await callback.answer()


@router.callback_query(F.data == "set:noop:news_topics")
async def cb_noop_news_topics(callback: CallbackQuery) -> None:
    await callback.answer("Открой /news_topics в меню команд", show_alert=True)


@router.callback_query(F.data.startswith("set:tz:"))
async def cb_pick_tz(callback: CallbackQuery) -> None:
    tz_value = callback.data[len("set:tz:"):]
    if not is_valid_tz(tz_value):
        await callback.answer("Неизвестный TZ", show_alert=True)
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, callback.from_user.id)
        owner.settings.timezone = tz_value
    await callback.answer(f"TZ: {tz_value}")
    await _refresh_section(callback, "tz")


@router.callback_query(F.data == "set:input:timezone")
async def cb_input_tz(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsStates.waiting_timezone)
    await callback.message.answer(
        "Введи название часового пояса в формате IANA, например <code>Europe/Moscow</code> или "
        "<code>Asia/Tashkent</code>. /cancel — отмена."
    )
    await callback.answer()


@router.message(SettingsStates.waiting_groq_key)
async def step_groq_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    if not key:
        await message.answer("Пустой ключ. Повтори или /cancel.")
        return
    try:
        await message.delete()
    except Exception:
        pass
    if not await GroqProvider(key).validate_key():
        await message.answer(
            "❌ Ключ не работает (Groq отклонил запрос). Проверь на console.groq.com. Повтори или /cancel."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await upsert_api_key(session, owner, "groq", key)
        owner.settings.llm_provider = "groq"
    await state.clear()
    await message.answer("✅ Groq key сохранён и выбран основным провайдером!")


@router.message(SettingsStates.waiting_openai_key)
async def step_openai_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    if not key:
        await message.answer("Пустой ключ. Повтори или /cancel.")
        return
    try:
        await message.delete()
    except Exception:
        pass
    if not await OpenAIProvider(key).validate_key():
        await message.answer("❌ Ключ не работает. Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await upsert_api_key(session, owner, "openai", key)
    await state.clear()
    await message.answer("✅ OpenAI key сохранён.")


@router.message(SettingsStates.waiting_gemini_key)
async def step_gemini_key(message: Message, state: FSMContext) -> None:
    key = (message.text or "").strip()
    if not key:
        await message.answer("Пустой ключ. Повтори или /cancel.")
        return
    try:
        await message.delete()
    except Exception:
        pass
    if not await GeminiProvider(key).validate_key():
        await message.answer("❌ Ключ не работает. Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        await upsert_api_key(session, owner, "gemini", key)
    await state.clear()
    await message.answer("✅ Gemini key сохранён.")


@router.message(SettingsStates.waiting_digest_time)
async def step_digest_time(message: Message, state: FSMContext) -> None:
    hm = (message.text or "").strip()
    if not HM_RE.match(hm):
        await message.answer("Формат HH:MM, например <code>06:30</code>. Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.digest_time = hm
    await state.clear()
    await message.answer(f"✅ Время дайджеста: <b>{hm} UTC</b>.")


@router.message(SettingsStates.waiting_news_time)
async def step_news_time(message: Message, state: FSMContext) -> None:
    hm = (message.text or "").strip()
    if not HM_RE.match(hm):
        await message.answer("Формат HH:MM, например <code>07:30</code>. Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.news_digest_time = hm
        tz = owner.settings.timezone
    await state.clear()
    await message.answer(f"✅ Время авто-новостей: <b>{hm}</b> · {tz_short(tz)}.")


@router.message(SettingsStates.waiting_auto_reply_text)
async def step_auto_reply_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return
    if len(text) > 1000:
        await message.answer("Слишком длинно (макс. 1000 символов). Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.auto_reply_text = text
    await state.clear()
    await message.answer(f"✅ Текст автоответа сохранён:\n<i>«{text}»</i>")


@router.message(SettingsStates.waiting_owner_persona)
async def step_owner_persona(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустой текст. Повтори или /cancel.")
        return
    if len(text) > 3000:
        await message.answer("Слишком длинно (макс. 3000 символов). Повтори или /cancel.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.owner_persona = text
    await state.clear()
    await message.answer(f"✅ <b>Инструкции сохранены!</b>\n\n<i>«{text}»</i>\n\nТеперь бот будет точно представлять твои интересы.")


@router.message(SettingsStates.waiting_timezone)
async def step_timezone(message: Message, state: FSMContext) -> None:
    tz_value = (message.text or "").strip()
    if not is_valid_tz(tz_value):
        await message.answer(
            "Не нашёл такой TZ. Используй IANA-формат, например <code>Europe/Moscow</code>. "
            "Список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones — "
            "колонка «TZ identifier». /cancel — отмена."
        )
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        owner.settings.timezone = tz_value
    await state.clear()
    await message.answer(f"✅ Часовой пояс: <b>{tz_short(tz_value)}</b>")
