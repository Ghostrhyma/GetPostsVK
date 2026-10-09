"""Модели БД.

Таблицы и колонки совпадают со старой версией бота, поэтому существующая
база подхватывается без миграций. Связей (relationship) намеренно нет: все
запросы явные, это исключает неожиданную ленивую подгрузку в async-сессиях.
"""
from __future__ import annotations

import ssl
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Chat(Base):
    """Чат Telegram или тема форума (message_thread_id)."""

    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id = mapped_column(BigInteger)
    message_thread_id: Mapped[int | None] = mapped_column(nullable=True)


class Domain(Base):
    """Страница VK, за которой следим."""

    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    group_name: Mapped[str] = mapped_column(String(1000))


class ChatDomain(Base):
    __tablename__ = "chat_domains"

    chat_id: Mapped[int] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True)


class Post(Base):
    """Уже обработанный пост: нужен, чтобы не отправлять его дважды."""

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"))
    check_key: Mapped[str] = mapped_column(String(150))
    created: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    attachment_type: Mapped[str] = mapped_column(nullable=False)
    url: Mapped[str] = mapped_column(nullable=False)
    preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))


class Database:
    """Движок SQLAlchemy и фабрика сессий."""

    def __init__(self, url: str, ssl_mode: str = "off"):
        kwargs: dict = {}
        if not url.startswith("sqlite"):
            # Контейнер живёт долго, а БД/пулер может закрывать простаивающие соединения
            kwargs.update(pool_pre_ping=True, pool_recycle=1800)
            if ssl_mode != "off":
                context = ssl.create_default_context()
                if ssl_mode == "require":
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                kwargs["connect_args"] = {"ssl": context}

        self.engine = create_async_engine(url, **kwargs)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_all(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
