import html

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.core.contact_resolver import resolve
from src.core.style_profile import update_style_profile_for_contact
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.router import build_provider
from src.userbot.manager import UserbotManager


router = Router(name="style_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("style"))
async def cmd_style(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return
    query = (command.args or "").strip()
    if not query:
        await message.answer("Использование: <code>/style имя контакта</code> — пересчитать профиль стиля общения.")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner)

    if provider is None:
        await message.answer("Сначала добавь LLM-ключ в /settings.")
        return

    candidates = await resolve(client, owner, query)
    if not candidates:
        await message.answer("Контакт не найден.")
        return
    target = candidates[0]
    profile = await update_style_profile_for_contact(provider, message.from_user.id, target.peer_id)
    lbl_esc = html.escape(target.label())
    if not profile:
        await message.answer(
            f"Не нашёл достаточно моих сообщений к <b>{lbl_esc}</b>. "
            "Сначала открой /chat и подгрузи историю."
        )
        return

    short = ", ".join(f"{k}: {v}" for k, v in list(profile.items())[:5] if isinstance(v, (str, int)))
    short_esc = html.escape(short)
    await message.answer(
        f"✅ Профиль стиля для <b>{lbl_esc}</b> обновлён.\n<i>{short_esc}</i>"
    )
