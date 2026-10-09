"""Фоновая проверка новых постов VK и рассылка их в чаты."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramMigrateToChat

from .config import Settings
from .posts import parse_post
from .schemas import ChatRef, DomainInfo
from .sender import deliver_post, is_dead_chat_error
from .vk import VkClient, VkException

if TYPE_CHECKING:  # нужен только для аннотаций, поэтому БД поллеру при импорте не нужна
    from .db.repo import Repo

log = logging.getLogger(__name__)

# Столько циклов подряд, в которых не получилось опросить НИ ОДНУ группу,
# считаем аварией и пишем администратору (один раз, а не каждую минуту)
ALERT_AFTER_FAILED_CYCLES = 3


class PostPoller:
    def __init__(self, bot: Bot, vk: VkClient, repo: Repo, settings: Settings):
        self._bot = bot
        self._vk = vk
        self._repo = repo
        self._settings = settings
        self._failed_cycles = 0
        self._alert_sent = False

    async def run(self) -> None:
        """Бесконечный цикл проверки. Останавливается отменой задачи."""
        log.info("Проверка постов запущена, интервал %s с", self._settings.poll_interval)
        while True:
            self._beat()
            try:
                total, failed = await self.run_once()
            except Exception:
                log.exception("Сбой цикла проверки постов (БД недоступна?)")
                total, failed = 1, 1
            await self._track_health(total, failed)
            self._beat()
            await asyncio.sleep(self._settings.poll_interval)

    async def run_once(self) -> tuple[int, int]:
        """Один проход по всем группам. Возвращает (всего групп, групп с ошибкой)."""
        await self._repo.cleanup_orphans()
        domains = await self._repo.list_domains()

        failed = 0
        for domain in domains:
            self._beat()
            try:
                await self._process_domain(domain)
            except VkException as exc:
                failed += 1
                log.warning("%s: %s", domain.name, exc)
            except Exception:
                failed += 1
                log.exception("%s: непредвиденная ошибка", domain.name)
        return len(domains), failed

    async def _process_domain(self, domain: DomainInfo) -> None:
        items = await self._vk.get_wall(domain.name, self._settings.posts_per_check)
        posts = [post for post in map(parse_post, items) if post is not None]
        if not posts:
            return

        # Посты помечаются как обработанные до отправки: если Telegram сбоит,
        # бот не будет бесконечно присылать один и тот же пост.
        to_send = await self._repo.register_new_posts(domain.id, posts)

        title = domain.group_name or domain.name
        for post in to_send:
            for chat in domain.chats:
                await self._send(chat, title, post)
                await asyncio.sleep(0.05)
            self._beat()

    async def _send(self, chat: ChatRef, title: str, post) -> None:
        try:
            await deliver_post(self._bot, chat, title, post)
        except TelegramMigrateToChat as exc:
            log.info("Чат %s стал супергруппой %s", chat.chat_id, exc.migrate_to_chat_id)
            await self._repo.migrate_chat(chat.db_id, exc.migrate_to_chat_id)
        except TelegramAPIError as exc:
            if is_dead_chat_error(exc):
                log.warning("Чат %s (тема %s) недоступен, отключаем: %s", chat.chat_id, chat.thread_id, exc)
                await self._repo.remove_chat(chat.db_id)
            else:
                log.exception("Не удалось отправить пост %s в чат %s", post.key, chat.chat_id)
        except Exception:
            log.exception("Не удалось отправить пост %s в чат %s", post.key, chat.chat_id)

    async def _track_health(self, total: int, failed: int) -> None:
        if total > 0 and failed == total:
            self._failed_cycles += 1
            if self._failed_cycles >= ALERT_AFTER_FAILED_CYCLES and not self._alert_sent:
                self._alert_sent = True
                await self._alert(
                    "⚠️ Бот не может получать посты: все запросы к VK/БД падают "
                    f"{self._failed_cycles} цикла подряд. Проверьте логи контейнера."
                )
            return

        self._failed_cycles = 0
        if self._alert_sent:
            self._alert_sent = False
            await self._alert("✅ Работа бота восстановилась.")

    async def _alert(self, text: str) -> None:
        admin_id = self._settings.admin_id
        if admin_id is None:
            return
        try:
            await self._bot.send_message(chat_id=admin_id, text=text)
        except Exception:
            log.exception("Не удалось отправить уведомление администратору")

    def _beat(self) -> None:
        """Отметка «жив» для healthcheck контейнера."""
        with contextlib.suppress(OSError):
            Path(self._settings.heartbeat_file).touch()
