"""Все запросы к БД."""
from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import delete, func, select

from ..posts import VkPost, split_new_posts
from ..schemas import ChatRef, DomainInfo, DomainRef
from .models import Attachment, Chat, ChatDomain, Domain, Post

log = logging.getLogger(__name__)

# Сколько последних ключей постов группы поднимать из БД для сверки
RECENT_KEYS_LIMIT = 300


class Repo:
    def __init__(self, sessionmaker):
        self._sm = sessionmaker

    # ------------------------------------------------------------ подключение групп

    async def link_domain(self, name: str, group_name: str, chat_id: int, thread_id: int | None) -> bool:
        """Привязывает группу VK к чату/теме. True, если привязка новая."""
        async with self._sm() as session:
            domain = await session.scalar(
                select(Domain).where(func.lower(Domain.name) == name.lower()).limit(1)
            )
            if domain is None:
                domain = Domain(name=name, group_name=group_name)
                session.add(domain)
            elif group_name and domain.group_name != group_name:
                domain.group_name = group_name

            chat = await session.scalar(
                select(Chat)
                .where(Chat.chat_id == chat_id, Chat.message_thread_id == thread_id)
                .limit(1)
            )
            if chat is None:
                chat = Chat(chat_id=chat_id, message_thread_id=thread_id)
                session.add(chat)

            await session.flush()  # получаем id новых строк

            link = await session.get(ChatDomain, (chat.id, domain.id))
            created = link is None
            if created:
                session.add(ChatDomain(chat_id=chat.id, domain_id=domain.id))

            await session.commit()
            return created

    async def list_domains_for_chat(self, chat_id: int, thread_id: int | None) -> list[DomainRef]:
        async with self._sm() as session:
            rows = (
                await session.execute(
                    select(Domain.id, Domain.name, Domain.group_name)
                    .join(ChatDomain, ChatDomain.domain_id == Domain.id)
                    .join(Chat, Chat.id == ChatDomain.chat_id)
                    .where(Chat.chat_id == chat_id, Chat.message_thread_id == thread_id)
                    .order_by(Domain.group_name)
                )
            ).all()
        return [DomainRef(row.id, row.name, row.group_name) for row in rows]

    async def unlink_domain(self, chat_id: int, thread_id: int | None, domain_id: int) -> bool:
        """Отвязывает группу от чата/темы. True, если связь была."""
        async with self._sm() as session:
            chat_row_id = await session.scalar(
                select(Chat.id)
                .where(Chat.chat_id == chat_id, Chat.message_thread_id == thread_id)
                .limit(1)
            )
            if chat_row_id is None:
                return False
            result = await session.execute(
                delete(ChatDomain).where(
                    ChatDomain.chat_id == chat_row_id, ChatDomain.domain_id == domain_id
                )
            )
            await session.commit()
            removed = result.rowcount > 0
        if removed:
            await self.cleanup_orphans()
        return removed

    # ------------------------------------------------------------ для поллера

    async def list_domains(self) -> list[DomainInfo]:
        async with self._sm() as session:
            domains = (
                await session.execute(
                    select(Domain.id, Domain.name, Domain.group_name).order_by(Domain.id)
                )
            ).all()
            links = (
                await session.execute(
                    select(ChatDomain.domain_id, Chat.id, Chat.chat_id, Chat.message_thread_id)
                    .select_from(ChatDomain)
                    .join(Chat, Chat.id == ChatDomain.chat_id)
                    .order_by(Chat.id)
                )
            ).all()

        chats: dict[int, list[ChatRef]] = {}
        for link in links:
            chats.setdefault(link.domain_id, []).append(
                ChatRef(db_id=link.id, chat_id=link.chat_id, thread_id=link.message_thread_id)
            )
        return [
            DomainInfo(d.id, d.name, d.group_name, tuple(chats.get(d.id, ())))
            for d in domains
        ]

    async def register_new_posts(self, domain_id: int, posts: Sequence[VkPost]) -> list[VkPost]:
        """Запоминает новые посты и возвращает те, что нужно отправить (старые - первыми)."""
        async with self._sm() as session:
            recent = list(
                (
                    await session.scalars(
                        select(Post.check_key)
                        .where(Post.domain_id == domain_id)
                        .order_by(Post.id.desc())
                        .limit(RECENT_KEYS_LIMIT)
                    )
                ).all()
            )
            new, to_send = split_new_posts(posts, recent)

            for post in new:
                row = Post(text=post.text, domain_id=domain_id, check_key=post.key)
                session.add(row)
                await session.flush()
                session.add_all(
                    Attachment(
                        attachment_type=media.kind,
                        url=media.url,
                        title=media.title,
                        preview=media.preview,
                        post_id=row.id,
                    )
                    for media in post.attachments
                )

            if new:
                await session.commit()
                log.info("Домен %s: новых постов %d, к отправке %d", domain_id, len(new), len(to_send))
            return to_send

    async def remove_chat(self, chat_row_id: int) -> None:
        """Полностью удаляет чат (например, бота из него выгнали)."""
        async with self._sm() as session:
            await session.execute(delete(ChatDomain).where(ChatDomain.chat_id == chat_row_id))
            await session.execute(delete(Chat).where(Chat.id == chat_row_id))
            await session.commit()
        await self.cleanup_orphans()

    async def migrate_chat(self, chat_row_id: int, new_chat_id: int) -> None:
        """Обычная группа стала супергруппой - у неё новый chat_id."""
        async with self._sm() as session:
            chat = await session.get(Chat, chat_row_id)
            if chat is not None:
                chat.chat_id = new_chat_id
                await session.commit()

    async def cleanup_orphans(self) -> tuple[int, int]:
        """Удаляет чаты без групп и группы без чатов (вместе с их постами)."""
        async with self._sm() as session:
            orphan_chats = (
                await session.scalars(
                    select(Chat.id).where(Chat.id.not_in(select(ChatDomain.chat_id)))
                )
            ).all()
            if orphan_chats:
                await session.execute(delete(Chat).where(Chat.id.in_(orphan_chats)))

            orphan_domains = (
                await session.scalars(
                    select(Domain.id).where(Domain.id.not_in(select(ChatDomain.domain_id)))
                )
            ).all()
            if orphan_domains:
                post_ids = select(Post.id).where(Post.domain_id.in_(orphan_domains))
                await session.execute(delete(Attachment).where(Attachment.post_id.in_(post_ids)))
                await session.execute(delete(Post).where(Post.domain_id.in_(orphan_domains)))
                await session.execute(delete(Domain).where(Domain.id.in_(orphan_domains)))

            if orphan_chats or orphan_domains:
                await session.commit()
                log.info(
                    "Очистка: удалено чатов %d, групп %d", len(orphan_chats), len(orphan_domains)
                )
            return len(orphan_chats), len(orphan_domains)
