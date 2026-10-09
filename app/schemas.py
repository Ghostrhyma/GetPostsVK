"""Простые структуры данных, которыми обмениваются слои приложения."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatRef:
    """Чат (или тема форума) Telegram, куда нужно слать посты."""

    db_id: int
    chat_id: int
    thread_id: int | None = None


@dataclass(frozen=True)
class DomainRef:
    """Подключённая группа VK (для списков в интерфейсе)."""

    id: int
    name: str
    group_name: str


@dataclass(frozen=True)
class DomainInfo:
    """Группа VK вместе со всеми чатами, в которые её нужно пересылать."""

    id: int
    name: str
    group_name: str
    chats: tuple[ChatRef, ...] = ()
