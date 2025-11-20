from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

post_keyboard = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Файлы поста", callback_data="files"), 
     InlineKeyboardButton(text="Видеофайлы поста", callback_data="videos")]
])


async def get_domains_keyboard(domains: list):
    dom_keyboard = InlineKeyboardBuilder()

    for domain in domains:
        dom_keyboard.add(InlineKeyboardButton(text=domain['group_name'], 
        callback_data=f"selectedDomain_{domain['id']}_{domain['group_name']}"))
    
    return dom_keyboard.adjust(1).as_markup()
    