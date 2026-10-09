"""Сборка и запуск бота."""
from __future__ import annotations

import asyncio
import logging
import sys
from urllib.parse import urlsplit

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllPrivateChats,
)

from .config import load_settings
from .db.models import Database
from .db.repo import Repo
from .handlers import router
from .poller import PostPoller
from .vk import VkClient

log = logging.getLogger("app")

COMMANDS = [
    BotCommand(command="start", description="Подключение бота к чату"),
    BotCommand(command="push_domain", description="Подключение группы по ссылке"),
    BotCommand(command="delete_domain", description="Отключение группы от чата"),
    BotCommand(command="cancel", description="Отменить ввод ссылки"),
]


async def _create_tables(db: Database, attempts: int = 10) -> None:
    """При старте контейнера БД может быть ещё не готова - ждём."""
    for attempt in range(1, attempts + 1):
        try:
            await db.create_all()
            return
        except Exception as exc:
            if attempt == attempts:
                raise
            log.warning("БД недоступна (%s), попытка %d/%d", exc, attempt, attempts)
            await asyncio.sleep(min(attempt * 2, 15))


async def _set_commands(bot: Bot) -> None:
    try:
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllChatAdministrators())
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except Exception:
        log.exception("Не удалось установить список команд")


def _on_poller_done(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Фоновая проверка постов остановилась", exc_info=task.exception())


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(settings.database_url, settings.db_ssl)
    await _create_tables(db)
    repo = Repo(db.sessionmaker)
    session = None
    if settings.telegram_proxy:
        # Прокси только для Telegram; запросы к VK идут напрямую. Нужен пакет aiohttp-socks
        session = AiohttpSession(proxy=settings.telegram_proxy)
        proxy_host = urlsplit(settings.telegram_proxy)
        log.info("Telegram API через прокси %s:%s", proxy_host.hostname, proxy_host.port)
    bot = Bot(token=settings.bot_token, session=session)

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        vk = VkClient(
            http,
            token=settings.vk_token,
            version=settings.vk_api_version,
            host=settings.vk_api_host,
            request_delay=settings.vk_request_delay,
        )
        dp = Dispatcher(repo=repo, vk=vk)
        dp.include_router(router)

        await _set_commands(bot)

        poller = asyncio.create_task(
            PostPoller(bot, vk, repo, settings).run(), name="post-poller"
        )
        poller.add_done_callback(_on_poller_done)
        log.info("Бот запущен, VK API: %s", settings.vk_api_host)

        try:
            # SIGTERM/SIGINT aiogram обрабатывает сам и аккуратно завершает polling
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        finally:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
            await bot.session.close()
            await db.dispose()
            log.info("Бот остановлен")
