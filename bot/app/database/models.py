import ssl
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from typing import List, Optional

from dotenv import load_dotenv
import os

load_dotenv()

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = True
ssl_ctx.verify_mode = ssl.CERT_REQUIRED

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_async_engine(
    url=DATABASE_URL,
    connect_args={"ssl": ssl_ctx})


async_session = async_sessionmaker(engine)


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Chat_Domain(Base):
    __tablename__ = "chat_domains"

    chat_id: Mapped[BigInteger] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True)


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id = mapped_column(BigInteger)
    message_thread_id: Mapped[Optional[int]] = mapped_column(nullable=True)

    domains_list: Mapped[List["Domain"]] = relationship(
        back_populates="chats_list",
        secondary="chat_domains"
    )


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    group_name: Mapped[str] = mapped_column(String(1000))

    posts_list: Mapped[List["Post"]] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan"
    )

    chats_list: Mapped[List["Chat"]] = relationship(
        back_populates="domains_list",
        secondary="chat_domains"
    )


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey('domains.id'))
    domain: Mapped[Domain] = relationship(back_populates="posts_list")
    check_key: Mapped[str] = mapped_column(String(150))
    created: Mapped[DateTime] = mapped_column(DateTime, default=func.now())
    updated: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    attachments: Mapped[List["Attachment"]] = relationship(
        back_populates="post",
        cascade="all, delete-orphan",
        passive_deletes=True
    )


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    attachment_type: Mapped[str] = mapped_column(nullable=False)
    url: Mapped[str] = mapped_column(nullable=False)
    preview: Mapped[Optional[str]] = mapped_column(Text,nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text,nullable=True)
    post_id: Mapped[int] = mapped_column(ForeignKey('posts.id', ondelete="CASCADE"))

    post: Mapped["Post"] = relationship(
        back_populates="attachments"   
    )


async def async_main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)