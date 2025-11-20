from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message, bot) -> bool:
        chat_id = message.chat.id
        user_id = message.from_user.id

        member = await bot.get_chat_member(chat_id, user_id)

        return member.status in ("administrator", "creator")