"""Проверка прав: команды бота доступны только администраторам чата."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter
from aiogram.types import Chat, Message

log = logging.getLogger(__name__)


async def is_chat_admin(bot: Bot, chat: Chat, user_id: int) -> bool:
    if chat.type == "private":
        return True
    try:
        member = await bot.get_chat_member(chat.id, user_id)
    except TelegramAPIError:
        log.exception("Не удалось получить статус участника %s в чате %s", user_id, chat.id)
        return False
    return member.status in ("administrator", "creator")


class IsAdmin(BaseFilter):
    """Пропускает сообщения администраторов (включая анонимных) и личные чаты."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        # Анонимный администратор пишет от имени самой группы
        if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
            return True
        if message.from_user is None:
            return False
        return await is_chat_admin(bot, message.chat, message.from_user.id)
