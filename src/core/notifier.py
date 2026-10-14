import logging
from typing import TYPE_CHECKING

from src.config import settings


if TYPE_CHECKING:
    from aiogram import Bot


logger = logging.getLogger(__name__)


class Notifier:
    # шлёт сообщения владельцу через control bot — используется userbot-кодом

    def __init__(self) -> None:
        self._bot: "Bot | None" = None

    def attach(self, bot: "Bot") -> None:
        self._bot = bot

    async def notify(self, text: str, *, parse_mode: str | None = "HTML") -> None:
        if self._bot is None:
            logger.warning("Notifier not attached, dropping message: %s", text[:80])
            return

        # Лимит Telegram на одно сообщение — 4096 символов
        max_chunk = 4000
        chunks = [text[i:i + max_chunk] for i in range(0, len(text), max_chunk)] if len(text) > max_chunk else [text]

        for chunk in chunks:
            try:
                await self._bot.send_message(
                    chat_id=settings.owner_telegram_id,
                    text=chunk,
                    parse_mode=parse_mode,
                )
            except Exception:
                if parse_mode is not None:
                    try:
                        # Fallback на plain text, если в HTML была ошибка разметки
                        await self._bot.send_message(
                            chat_id=settings.owner_telegram_id,
                            text=chunk,
                            parse_mode=None,
                        )
                        continue
                    except Exception:
                        pass
                logger.exception("Failed to notify owner")


notifier = Notifier()
