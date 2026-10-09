"""Отправка поста в чат Telegram."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import InputMediaPhoto

from . import texts
from .keyboards import attachments_keyboard
from .posts import VkPost, chunk_album, split_text
from .schemas import ChatRef

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
ALBUM_LIMIT = 10
_ATTEMPTS = 3

# Ошибки BadRequest, после которых чат/тему больше нет смысла опрашивать
_DEAD_CHAT_MARKERS = ("chat not found", "message thread not found", "topic_deleted", "topic deleted")


def is_dead_chat_error(exc: BaseException) -> bool:
    """Бота выгнали, чат или тема удалены - отправлять туда больше нельзя."""
    if isinstance(exc, TelegramForbiddenError):
        return True
    if isinstance(exc, TelegramBadRequest):
        message = (exc.message or "").lower()
        return any(marker in message for marker in _DEAD_CHAT_MARKERS)
    return False


async def _call(method, **kwargs):
    """Вызов метода бота с повтором при flood-лимите и сетевых сбоях."""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return await method(**kwargs)
        except TelegramRetryAfter as exc:
            if attempt == _ATTEMPTS:
                raise
            log.warning("Flood-лимит Telegram, ждём %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
        except TelegramNetworkError:
            if attempt == _ATTEMPTS:
                raise
            await asyncio.sleep(attempt * 2)


async def deliver_post(bot: Bot, chat: ChatRef, title: str, post: VkPost) -> None:
    """Отправляет пост: фото/превью видео, подпись, кнопки с файлами и видео.

    Если фото отправить не удалось (например, Telegram не смог скачать картинку),
    пост уходит текстом, чтобы он не потерялся.
    """
    text = f"{title}\n\n{post.text}" if post.text else title

    photos = [m.url for m in post.attachments if m.kind == "photo"]
    files = [m for m in post.attachments if m.kind == "doc"]
    videos = [m for m in post.attachments if m.kind == "video"]
    keyboard = attachments_keyboard(files, videos)
    media = photos or ([videos[0].preview] if videos and videos[0].preview else [])

    target = {"chat_id": chat.chat_id, "message_thread_id": chat.thread_id}
    caption_fits = len(text) <= CAPTION_LIMIT
    text_done = False
    keyboard_done = keyboard is None

    if media:
        try:
            for index, batch in enumerate(chunk_album(media, ALBUM_LIMIT)):
                caption = text if index == 0 and caption_fits else None
                if len(batch) == 1:
                    # Клавиатуру можно прикрепить только к одиночному фото с подписью
                    attach = caption is not None and keyboard is not None
                    await _call(
                        bot.send_photo,
                        photo=batch[0],
                        caption=caption,
                        reply_markup=keyboard if attach else None,
                        **target,
                    )
                    keyboard_done = keyboard_done or attach
                else:
                    group = [
                        InputMediaPhoto(media=url, caption=caption if position == 0 else None)
                        for position, url in enumerate(batch)
                    ]
                    await _call(bot.send_media_group, media=group, **target)
                if index == 0 and caption_fits:
                    text_done = True
        except TelegramBadRequest as exc:
            if is_dead_chat_error(exc):
                raise
            log.warning("Не удалось отправить медиа поста %s: %s. Отправляем текстом", post.key, exc)

    if not text_done:
        chunks = split_text(text, MESSAGE_LIMIT)
        for position, chunk in enumerate(chunks):
            is_last = position == len(chunks) - 1
            await _call(
                bot.send_message,
                text=chunk,
                reply_markup=keyboard if is_last else None,
                **target,
            )
    elif not keyboard_done:
        await _call(bot.send_message, text=texts.ATTACHMENTS, reply_markup=keyboard, **target)
