from app.database.models import async_session
from app.database.models import Chat, Domain, Chat_Domain, Attachment, Post
from app.forapi.app_api import get_name_of_group

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import logging



async def set_chat(tg_id, thread_id):
    async with async_session() as session:
        chat = await session.scalar(select(Chat).where(Chat.id == tg_id))

        if not chat:
            session.add(Chat(id=tg_id, message_thread_id=thread_id))
            await session.commit()


async def get_chat_id(chat_id, message_thread_id):
    async with async_session() as session:
        chat = await session.scalar(
            select(Chat)
            .where(Chat.chat_id == chat_id)
            .where(Chat.message_thread_id == message_thread_id)
        )

        back_id = chat.id
        return back_id


async def clean_up_orphan_domains():
    async with async_session() as session:
        domain_stmt = select(Domain).where(~Domain.chats_list.any())
        result = await session.scalars(domain_stmt)
        orphans = result.all()

        for domain in orphans:
            await session.delete(domain)

        if orphans:
            await session.commit()
            logging.info(f"Было удалено неиспользуемых доменов: {len(orphans)}")
        else:
            logging.info("Неиспользуемых доменов не обнаружено")


async def clean_up_orphan_chats():
    async with async_session() as session:
        chat_stmt = select(Chat).where(~Chat.domains_list.any())
        result = await session.scalars(chat_stmt)
        orphans = result.all()

        for chat in orphans:
            await session.delete(chat)

        if orphans:
            await session.commit()
            logging.info(f"Было удалено неиспользуемых чатов: {len(orphans)}")
        else:
            logging.info("Неиспользуемых чатов не обнаружено")



async def delete_chat_domain_link(chat_id, domain_id):
    async with async_session() as session:
        try:
            # Ищем связь в таблице chat_domains
            stmt = select(Chat_Domain).where(
                Chat_Domain.chat_id == chat_id,
                Chat_Domain.domain_id == domain_id
            )
            result = await session.execute(stmt)
            link = result.scalars().first()

            if link:
                await session.delete(link)
                await session.commit()
                logging.info(f"Домен {domain_id} удалён из чата {chat_id}")
            else:
                logging.warning("Связь не найдена")
        except Exception as e:
            await session.rollback()
            logging.error(f"{e}", exc_info=True)


async def set_domain(name, new_chat):
    async with async_session() as session:
        group_name = await get_name_of_group(name)

        domain = await session.scalar(
            select(Domain)
            .options(selectinload(Domain.chats_list))
            .where(Domain.name == name)
            )
        
        chat = await session.scalar(select(Chat)
                                    .where(Chat.chat_id == new_chat.chat_id )
                                    .where(Chat.message_thread_id == new_chat.message_thread_id))

        if not domain:
            domain = Domain(name=name, group_name=group_name)
            session.add(domain)

        if not chat:
            chat = new_chat
            session.add(chat)

        if chat not in domain.chats_list:
            domain.chats_list.append(chat)

        await session.commit()


async def get_domains_for_chat(chat_id, message_thread_id):
    async with async_session() as session:
        result = await session.scalar(
            select(Chat)
            .options(selectinload(Chat.domains_list))
            .where(Chat.chat_id == chat_id)
            .where(Chat.message_thread_id == message_thread_id)
        )

        try:
            domains = [{
                "id": domain.id,
                "name": domain.name,
                "group_name": domain.group_name,
            }
            for domain in result.domains_list]

            return domains
        except AttributeError:
            return False


async def get_domains():
    async with async_session() as session:
        result = await session.scalars(
            select(Domain)
            .options(
                selectinload(Domain.chats_list),
                selectinload(Domain.posts_list)
            )
        )
        domains = result.unique().all()

        domain_data = []
        for domain in domains:
            chats = [
                {"chat_id": chat.chat_id, "message_thread_id": chat.message_thread_id}
                for chat in domain.chats_list
            ]

            domain_data.append({
                "id": domain.id,
                "name": domain.name,
                "group_name": domain.group_name,
                "chats": chats,
            })

        return domain_data
    

async def chech_post_in_db(post_data):
    async with async_session() as session:
        last_posts = await session.scalars(
            select(Post)
            .where(Post.domain_id == post_data["domain_id"])
        )

        checkINDB = False
        for l_post in last_posts:
            if l_post and l_post.check_key == post_data["check_key"]:
                logging.info(f"Пост для домена с ID={post_data['domain_id']} уже существует в БД")
                checkINDB = True
                return checkINDB
            
        if not checkINDB:
            new_post = Post(
                    text=post_data["text"],
                    domain_id=post_data["domain_id"],
                    check_key=post_data["check_key"],
                    attachments=[
                        Attachment(
                            attachment_type=a["type"],
                            url=a["url"],
                            title=a.get("title"),
                            preview=a.get("preview")
                        )
                        for a in post_data["attachments"]
                    ]
                )
            
            session.add(new_post)
            await session.commit()
            logging.info(f"Обнаружен новый пост для домена с ID={post_data['domain_id']}")
            return False
            

async def create_post_from_json(post_json, domain_id):
    try:
        copy_data = post_json.get("copy_history", [post_json])[0]
        text = copy_data.get("text", ".") or "."

        attachments = []
        for item in copy_data.get("attachments", []):
            if item["type"] == "photo":
                for photo in item["photo"]["sizes"]:
                    if photo["type"] == "x":
                        attachments.append({
                            "type": "photo",
                            "url": photo["url"]
                        })
            elif item["type"] == "doc":
                attachments.append({
                    "type": "doc",
                    "url": item["doc"]["url"],
                    "title": item["doc"]["title"]
                })
            elif item["type"] == "video":
                owner_id = item["video"]["owner_id"]
                video_id = item["video"]["id"]
                preview = item["video"]["image"][3]["url"]
                access_key = item["video"].get("access_key")
                link = f"https://vk.com/video{owner_id}_{video_id}" + (f"?access_key={access_key}" if access_key else "")
                attachments.append({
                    "type": "video",
                    "url": link,
                    "preview": preview
                })

        return {
            "text": text,
            "domain_id": domain_id,
            "attachments": attachments,
            "check_key": f"{copy_data['owner_id']}-{copy_data['id']}"
        }

    except Exception as e:
        logging.error("Ошибка в create_post_from_json:", exc_info=True)
        return None