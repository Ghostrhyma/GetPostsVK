from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


from app.forapi.app_api import get_last_post_from_group
import app.keyboards as kb
import app.database.requests as req
from app.database.models import Chat
import app.textsForCmd as tfc
from app.bot_filters import IsAdmin


import re, logging


router = Router()
router.message.filter(IsAdmin())


class DomainData(StatesGroup):
    domain = State()


@router.message(Command("start"))
async def get_start(message: Message):
    await message.answer(tfc.for_start, parse_mode="HTML")


@router.message(Command("push_domain"))
async def get_domain(message: Message, state: FSMContext):
    await state.set_state(DomainData.domain)
    await message.answer("Введите ссылку на группу, посты которой хотите получать")
    logging.info("Ожидаем домен от пользователя")


answ = "Выберите группу ВК, которую хотите открепить от чата"

@router.message(Command("delete_domain"))
async def delete_domain(message: Message):
    data = await req.get_domains_for_chat(message.chat.id, message.message_thread_id)
    if not isinstance(data, bool) or not data == []:
        await message.answer(answ, 
                             reply_markup=await kb.get_domains_keyboard(data))
        logging.info("Ожидаем домен от пользователя для удаления")
    else:
        await message.answer("К вашему чату не привязано ни одной группы ВК")


@router.callback_query(F.data.startswith('selectedDomain_'))
async def deleting_domain(callback: CallbackQuery):
    chat_id = await req.get_chat_id(callback.message.chat.id, callback.message.message_thread_id)
    domain_id = callback.data.split('_')[1]
    await callback.answer()
    await req.delete_chat_domain_link(chat_id=chat_id, domain_id=int(domain_id))
    await callback.message.answer(f"Группа откреплена от данного чата/темы")



@router.message(DomainData.domain)
async def after_get_domain(message: Message, state: FSMContext):
    """
        Пометка на будущее

        Если бот интегрируется с чатом или супергруппой, то боту нужно выдать права администратора

    """
    # -------- Вынести в отдельную функцию --------------
    vk_pattern = "https://vk.com/([^/?#]+)"
    match = re.search(vk_pattern, message.text)
    logging.info("Проверка домена выполнена")
    # ---------------------------------------------------

    if match:
        from_chat_domain = match.group(1)
        logging.info(f"Получен домен {from_chat_domain}")
        await state.update_data(domain=from_chat_domain)
        data = await state.get_data()

        domain = data["domain"]

        new_chat = Chat(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id
        )

        logging.info(f"Отправлен запрос для подтверждения существования группы с доменом '{domain}'")
        resp = await get_last_post_from_group(domain=domain)
        
        # Позже разбить код ниже на подфункции

        if isinstance(resp, bool):
            logging.warning(f"{domain} -> Такой группы не существует или у группы отсутствуют посты")
            await message.answer("Такой группы не существует или у группы отсутствуют посты")
        else:
            # domains_list = await read_from_json("app/forapi/domains.json")
            await req.set_domain(domain, new_chat)
            await message.answer("Ссылка успешно обработана")
            logging.info(f"{domain} -> Ссылка успешно обработана")
            await state.clear()
    else:
        await message.answer("Убедитесь, что предоставленная ссылка корректна")
        logging.warning("Предоставленная ссылка некорректна")






# Ответы на рандомные сообщения пользователя

@router.message((F.text.lower() == "penis") | (F.text.lower() == "пенис"))
async def exclipt_ans(message: Message):
    await message.answer("А ты оригинален")

@router.message((F.text.lower() == "парадокси") | (F.text.lower() == "paradoxy"))
async def exclipt_ans(message: Message):
    await message.answer("Думаю она даже не целоваолась")

@router.message((F.text.lower() == "а ты оригинален"))
async def exclipt_ans(message: Message):
    await message.answer("Пенис")
