"""Тесты слоя БД на SQLite (нужны sqlalchemy и aiosqlite: pip install -r requirements-dev.txt)."""
import importlib.util
import tempfile
import unittest

HAVE_DEPS = all(importlib.util.find_spec(name) for name in ("sqlalchemy", "aiosqlite"))

if HAVE_DEPS:
    from sqlalchemy import func, select

    from app.db.models import Attachment, Chat, ChatDomain, Database, Domain, Post
    from app.db.repo import Repo
    from app.posts import Media, VkPost


def vk_post(post_id, *attachments):
    return VkPost(-100, post_id, post_id, f"пост {post_id}", tuple(attachments))


@unittest.skipUnless(HAVE_DEPS, "нужны sqlalchemy и aiosqlite")
class RepoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(f"sqlite+aiosqlite:///{self.tmp.name}/test.db")
        await self.db.create_all()
        self.repo = Repo(self.db.sessionmaker)

    async def asyncTearDown(self):
        await self.db.dispose()
        self.tmp.cleanup()

    async def count(self, model):
        async with self.db.sessionmaker() as session:
            return await session.scalar(select(func.count()).select_from(model))

    async def test_link_domain(self):
        self.assertTrue(await self.repo.link_domain("durov", "Павел Дуров", -1001, None))
        self.assertFalse(await self.repo.link_domain("durov", "Павел Дуров", -1001, None))

        domains = await self.repo.list_domains_for_chat(-1001, None)
        self.assertEqual([(d.name, d.group_name) for d in domains], [("durov", "Павел Дуров")])
        self.assertEqual(await self.repo.list_domains_for_chat(-1001, 5), [])
        self.assertEqual(await self.count(Chat), 1)

    async def test_topics_of_one_forum_are_separate_chats(self):
        await self.repo.link_domain("a", "A", -1001, None)
        await self.repo.link_domain("a", "A", -1001, 5)
        self.assertEqual(await self.count(Chat), 2)
        self.assertEqual(await self.count(Domain), 1)

    async def test_domain_lookup_is_case_insensitive(self):
        await self.repo.link_domain("Durov", "Старое имя", -1, None)
        await self.repo.link_domain("durov", "Новое имя", -2, None)
        self.assertEqual(await self.count(Domain), 1)
        domains = await self.repo.list_domains()
        self.assertEqual(domains[0].group_name, "Новое имя")
        self.assertEqual(len(domains[0].chats), 2)

    async def test_list_domains_contains_chats(self):
        await self.repo.link_domain("a", "A", -1, None)
        await self.repo.link_domain("a", "A", -2, 9)
        await self.repo.link_domain("b", "B", -2, 9)
        by_name = {d.name: d for d in await self.repo.list_domains()}
        self.assertEqual({(c.chat_id, c.thread_id) for c in by_name["a"].chats}, {(-1, None), (-2, 9)})
        self.assertEqual({(c.chat_id, c.thread_id) for c in by_name["b"].chats}, {(-2, 9)})

    async def test_register_new_posts_flow(self):
        await self.repo.link_domain("a", "A", -1, None)
        domain_id = (await self.repo.list_domains())[0].id

        first = await self.repo.register_new_posts(domain_id, [vk_post(1), vk_post(2), vk_post(3)])
        self.assertEqual([p.post_id for p in first], [3])
        self.assertEqual(await self.count(Post), 3)

        self.assertEqual(await self.repo.register_new_posts(domain_id, [vk_post(2), vk_post(3)]), [])

        later = await self.repo.register_new_posts(domain_id, [vk_post(5), vk_post(4), vk_post(3)])
        self.assertEqual([p.post_id for p in later], [4, 5])
        self.assertEqual(await self.count(Post), 5)

    async def test_attachments_are_stored(self):
        await self.repo.link_domain("a", "A", -1, None)
        domain_id = (await self.repo.list_domains())[0].id
        media = (Media("photo", "https://img/1"), Media("video", "https://vk.ru/video-1_2", preview="p"))
        await self.repo.register_new_posts(domain_id, [vk_post(1, *media)])
        async with self.db.sessionmaker() as session:
            rows = (await session.scalars(select(Attachment).order_by(Attachment.id))).all()
        self.assertEqual([(r.attachment_type, r.url, r.preview) for r in rows],
                         [("photo", "https://img/1", None), ("video", "https://vk.ru/video-1_2", "p")])

    async def test_unlink_cleans_up_domain_chat_and_posts(self):
        await self.repo.link_domain("a", "A", -1, None)
        domain_id = (await self.repo.list_domains())[0].id
        await self.repo.register_new_posts(domain_id, [vk_post(1, Media("photo", "u"))])

        self.assertTrue(await self.repo.unlink_domain(-1, None, domain_id))
        for model in (Chat, Domain, ChatDomain, Post, Attachment):
            self.assertEqual(await self.count(model), 0, model.__name__)

    async def test_unlink_keeps_domain_used_by_another_chat(self):
        await self.repo.link_domain("a", "A", -1, None)
        await self.repo.link_domain("a", "A", -2, None)
        domain_id = (await self.repo.list_domains())[0].id

        self.assertTrue(await self.repo.unlink_domain(-1, None, domain_id))
        self.assertEqual(await self.count(Domain), 1)
        self.assertEqual(await self.count(Chat), 1)

    async def test_unlink_unknown(self):
        self.assertFalse(await self.repo.unlink_domain(-1, None, 1))
        await self.repo.link_domain("a", "A", -1, None)
        self.assertFalse(await self.repo.unlink_domain(-1, None, 12345))

    async def test_remove_chat(self):
        await self.repo.link_domain("a", "A", -1, None)
        await self.repo.link_domain("a", "A", -2, None)
        chat_row_id = (await self.repo.list_domains())[0].chats[0].db_id
        await self.repo.remove_chat(chat_row_id)
        self.assertEqual(await self.count(Chat), 1)
        self.assertEqual(await self.count(Domain), 1)

    async def test_migrate_chat(self):
        await self.repo.link_domain("a", "A", -1, None)
        chat_row_id = (await self.repo.list_domains())[0].chats[0].db_id
        await self.repo.migrate_chat(chat_row_id, -100777)
        self.assertEqual((await self.repo.list_domains())[0].chats[0].chat_id, -100777)

    async def test_cleanup_orphans(self):
        async with self.db.sessionmaker() as session:
            session.add_all([Chat(chat_id=1), Domain(name="x", group_name="X")])
            await session.commit()
        self.assertEqual(await self.repo.cleanup_orphans(), (1, 1))
        self.assertEqual(await self.repo.cleanup_orphans(), (0, 0))


if __name__ == "__main__":
    unittest.main()
