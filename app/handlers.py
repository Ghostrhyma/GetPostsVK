"""Команды бота: подключение и отключение групп VK."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from . import texts
from .db.repo import Repo
from .filters import IsAdmin, is_chat_admin
from .keyboards import DOMAIN_CALLBACK_PREFIX, domains_keyboard
from .posts import extract_domain
from .vk import VkClient, VkError, VkUnavailable

log = logging.getLogger(__name__)

router = Router(name="main")


class DomainForm(StatesGroup):
    domain = State()


def topic_id(message: Message) -> int | None:
    """id темы форума или None.

    В обычных супергруппах message_thread_id заполнен и у ответов на сообщения,
    но это не тема, поэтому учитываем его только для настоящих тем.
    """
    return message.message_thread_id if message.is_topic_message else None


# ---------------------------------------------------------------- команды
# IsAdmin стоит последним в списке фильтров: он делает запрос к Telegram и
# должен выполняться только для сообщений, которые уже подошли по остальным условиям.


@router.message(Command("start"), IsAdmin())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.START, parse_mode="HTML")


@router.message(Command("cancel"), IsAdmin())
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.CANCELLED)


@router.message(Command("push_domain"), IsAdmin())
async def cmd_push_domain(message: Message, state: FSMContext):
    await state.set_state(DomainForm.domain)
    await message.answer(texts.ASK_LINK)
    log.info("Чат %s: ожидаем ссылку на группу", message.chat.id)


@router.message(Command("delete_domain"), IsAdmin())
async def cmd_delete_domain(message: Message, state: FSMContext, repo: Repo):
    await state.clear()
    domains = await repo.list_domains_for_chat(message.chat.id, topic_id(message))
    if not domains:
        await message.answer(texts.NO_DOMAINS)
        return
    await message.answer(texts.CHOOSE_DOMAIN, reply_markup=domains_keyboard(domains))


@router.callback_query(F.data.startswith(DOMAIN_CALLBACK_PREFIX))
async def on_domain_selected(callback: CallbackQuery, bot, repo: Repo):
    message = callback.message
    if not isinstance(message, Message):  # сообщение слишком старое / недоступно
        await callback.answer()
        return

    # У callback-запросов нет фильтра IsAdmin, поэтому права проверяем здесь
    if not await is_chat_admin(bot, message.chat, callback.from_user.id):
        await callback.answer(texts.ADMIN_ONLY, show_alert=True)
        return

    try:
        domain_id = int(callback.data[len(DOMAIN_CALLBACK_PREFIX):])
    except ValueError:
        await callback.answer()
        return

    await repo.unlink_domain(message.chat.id, topic_id(message), domain_id)
    await callback.answer()
    await message.edit_text(texts.UNLINKED)
    log.info("Чат %s: группа %s откреплена", message.chat.id, domain_id)


# ---------------------------------------------------------------- ввод ссылки


@router.message(DomainForm.domain, F.text, IsAdmin())
async def receive_domain(message: Message, state: FSMContext, repo: Repo, vk: VkClient):
    domain = extract_domain(message.text)
    if domain is None:
        await message.answer(texts.BAD_LINK)
        log.warning("Некорректная ссылка: %r", message.text)
        return

    log.info("Проверяем группу %s", domain)
    try:
        posts = await vk.get_wall(domain, 5)
    except VkError as exc:
        log.warning("%s: %s", domain, exc)
        await message.answer(texts.NOT_FOUND if exc.inaccessible else texts.VK_FAILED)
        return
    except VkUnavailable:
        log.exception("VK недоступен при проверке %s", domain)
        await message.answer(texts.VK_DOWN)
        return

    if not posts:
        await message.answer(texts.NO_POSTS)
        return

    try:
        group_name = await vk.get_name(domain) or domain
    except VkUnavailable:
        group_name = domain

    await repo.link_domain(domain, group_name, message.chat.id, topic_id(message))
    await state.clear()
    await message.answer(f"Группа «{group_name}» подключена. Новые посты будут приходить сюда.")
    log.info("%s -> подключена к чату %s", domain, message.chat.id)


# ---------------------------------------------------------------- ответы на фразы
# Шутливые ответы из старой версии бота.


@router.message(F.text.lower().in_({"penis", "пенис"}), IsAdmin())
async def reply_penis(message: Message):
    await message.answer("А ты оригинален")


@router.message(F.text.lower().in_({"парадокси", "paradoxy"}), IsAdmin())
async def reply_paradoxy(message: Message):
    await message.answer("Думаю она даже не целоваолась")


@router.message(F.text.lower() == "а ты оригинален", IsAdmin())
async def reply_original(message: Message):
    await message.answer("Пенис")
