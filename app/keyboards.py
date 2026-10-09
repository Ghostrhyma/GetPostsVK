"""Inline-клавиатуры."""
from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .posts import Media
from .schemas import DomainRef

# Префикс совпадает со старой версией: кнопки в уже отправленных сообщениях продолжат работать
DOMAIN_CALLBACK_PREFIX = "selectedDomain_"

MAX_URL_BUTTONS = 20
_BUTTON_TEXT_LIMIT = 60


def _short(text: str) -> str:
    return text if len(text) <= _BUTTON_TEXT_LIMIT else text[: _BUTTON_TEXT_LIMIT - 1] + "…"


def domains_keyboard(domains: Sequence[DomainRef]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_short(domain.group_name),
                    callback_data=f"{DOMAIN_CALLBACK_PREFIX}{domain.id}",
                )
            ]
            for domain in domains
        ]
    )


def attachments_keyboard(files: Sequence[Media], videos: Sequence[Media]) -> InlineKeyboardMarkup | None:
    """Кнопки-ссылки на документы и видео из поста (по две в ряд)."""
    buttons = [
        InlineKeyboardButton(text=_short(f"Файл из поста - {file.title}"), url=file.url)
        for file in files
    ]
    for number, video in enumerate(videos, start=1):
        label = "Видео из поста" if len(videos) == 1 else f"Видео из поста {number}"
        buttons.append(InlineKeyboardButton(text=label, url=video.url))

    buttons = buttons[:MAX_URL_BUTTONS]
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    )
