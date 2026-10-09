import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramForbiddenError, TelegramMigrateToChat

from app.config import Settings
from app.poller import ALERT_AFTER_FAILED_CYCLES, PostPoller
from app.posts import split_new_posts
from app.schemas import ChatRef, DomainInfo
from app.vk import VkError, VkUnavailable


def item(post_id, text="", date=None):
    return {"id": post_id, "owner_id": -100, "date": date or post_id, "text": text or f"пост {post_id}"}


class FakeRepo:
    def __init__(self, domains):
        self.domains = domains
        self.recent = {}
        self.removed = []
        self.migrated = []

    async def cleanup_orphans(self):
        return 0, 0

    async def list_domains(self):
        return self.domains

    async def register_new_posts(self, domain_id, posts):
        recent = self.recent.setdefault(domain_id, [])
        new, to_send = split_new_posts(posts, recent)
        for post in new:
            recent.insert(0, post.key)
        return to_send

    async def remove_chat(self, chat_row_id):
        self.removed.append(chat_row_id)

    async def migrate_chat(self, chat_row_id, new_chat_id):
        self.migrated.append((chat_row_id, new_chat_id))


class FakeVk:
    def __init__(self, walls):
        self.walls = walls

    async def get_wall(self, domain, count):
        result = self.walls[domain]
        if isinstance(result, Exception):
            raise result
        return result


class FakeBot:
    def __init__(self, fail_for=None):
        self.sent = []
        self.fail_for = fail_for or {}

    async def send_message(self, **kwargs):
        exc = self.fail_for.get(kwargs["chat_id"])
        if exc:
            raise exc
        self.sent.append(kwargs)

    def texts(self, chat_id):
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]


def domain(domain_id, name, *chat_ids):
    chats = tuple(ChatRef(db_id=chat_id, chat_id=chat_id) for chat_id in chat_ids)
    return DomainInfo(domain_id, name, f"Группа {name}", chats)


class PollerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("app.poller.asyncio.sleep", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.heartbeat = os.path.join(tmp.name, "heartbeat")
        self.settings = Settings(
            bot_token="t", vk_token="v", database_url="sqlite+aiosqlite://",
            admin_id=999, heartbeat_file=self.heartbeat,
        )

    def poller(self, domains, walls, bot=None):
        self.repo = FakeRepo(domains)
        self.bot = bot or FakeBot()
        return PostPoller(self.bot, FakeVk(walls), self.repo, self.settings)

    async def test_first_cycle_sends_only_newest_then_only_new_posts(self):
        walls = {"a": [item(3), item(2), item(1)]}
        poller = self.poller([domain(1, "a", -1, -2)], walls)

        self.assertEqual(await poller.run_once(), (1, 0))
        self.assertEqual(self.bot.texts(-1), ["Группа a\n\nпост 3"])
        self.assertEqual(self.bot.texts(-2), ["Группа a\n\nпост 3"])

        await poller.run_once()  # ничего нового
        self.assertEqual(len(self.bot.sent), 2)

        walls["a"] = [item(5), item(4), item(3), item(2)]
        await poller.run_once()
        self.assertEqual(
            self.bot.texts(-1), ["Группа a\n\nпост 3", "Группа a\n\nпост 4", "Группа a\n\nпост 5"]
        )

    async def test_broken_vk_items_are_skipped(self):
        poller = self.poller([domain(1, "a", -1)], {"a": [{"garbage": True}, item(1)]})
        await poller.run_once()
        self.assertEqual(self.bot.texts(-1), ["Группа a\n\nпост 1"])

    async def test_failing_group_does_not_stop_the_others(self):
        walls = {
            "closed": VkError(15, "Access denied"),
            "down": VkUnavailable("timeout"),
            "ok": [item(1)],
        }
        poller = self.poller(
            [domain(1, "closed", -1), domain(2, "down", -2), domain(3, "ok", -3)], walls
        )
        self.assertEqual(await poller.run_once(), (3, 2))
        self.assertEqual(self.bot.texts(-3), ["Группа ok\n\nпост 1"])

    async def test_unexpected_error_in_one_group_is_isolated(self):
        walls = {"bad": RuntimeError("boom"), "ok": [item(1)]}
        poller = self.poller([domain(1, "bad", -1), domain(2, "ok", -2)], walls)
        self.assertEqual(await poller.run_once(), (2, 1))
        self.assertEqual(len(self.bot.texts(-2)), 1)

    async def test_kicked_chat_is_removed_and_others_still_served(self):
        kicked = TelegramForbiddenError(method=None, message="Forbidden: bot was kicked from the group chat")
        poller = self.poller(
            [domain(1, "a", -1, -2)], {"a": [item(1)]}, bot=FakeBot(fail_for={-1: kicked})
        )
        await poller.run_once()
        self.assertEqual(self.repo.removed, [-1])
        self.assertEqual(len(self.bot.texts(-2)), 1)

    async def test_group_upgraded_to_supergroup_is_migrated(self):
        migrate = TelegramMigrateToChat(
            method=None, message="Bad Request: group chat was upgraded to a supergroup chat",
            migrate_to_chat_id=-100555,
        )
        poller = self.poller([domain(1, "a", -1)], {"a": [item(1)]}, bot=FakeBot(fail_for={-1: migrate}))
        await poller.run_once()
        self.assertEqual(self.repo.migrated, [(-1, -100555)])
        self.assertEqual(self.repo.removed, [])

    async def test_empty_wall_is_fine(self):
        poller = self.poller([domain(1, "a", -1)], {"a": []})
        self.assertEqual(await poller.run_once(), (1, 0))
        self.assertEqual(self.bot.sent, [])

    async def test_no_groups_is_not_a_failure(self):
        poller = self.poller([], {})
        total, failed = await poller.run_once()
        await poller._track_health(total, failed)
        self.assertEqual((total, failed), (0, 0))
        self.assertEqual(self.bot.sent, [])

    async def test_admin_alert_once_then_recovery(self):
        poller = self.poller([domain(1, "a", -1)], {"a": VkUnavailable("down")})

        for _ in range(ALERT_AFTER_FAILED_CYCLES - 1):
            await poller._track_health(*await poller.run_once())
        self.assertEqual(self.bot.texts(999), [])

        for _ in range(5):  # дальше - тишина, а не спам
            await poller._track_health(*await poller.run_once())
        self.assertEqual(len(self.bot.texts(999)), 1)
        self.assertIn("⚠️", self.bot.texts(999)[0])

        poller._vk.walls["a"] = [item(1)]
        await poller._track_health(*await poller.run_once())
        self.assertEqual(len(self.bot.texts(999)), 2)
        self.assertIn("✅", self.bot.texts(999)[1])

    async def test_partial_failure_is_not_an_outage(self):
        walls = {"closed": VkError(15, "x"), "ok": [item(1)]}
        poller = self.poller([domain(1, "closed", -1), domain(2, "ok", -2)], walls)
        for _ in range(ALERT_AFTER_FAILED_CYCLES + 2):
            await poller._track_health(*await poller.run_once())
        self.assertEqual(self.bot.texts(999), [])

    async def test_no_alert_without_admin_id(self):
        self.settings = Settings(**{**self.settings.__dict__, "admin_id": None})
        poller = self.poller([domain(1, "a", -1)], {"a": VkUnavailable("down")})
        for _ in range(ALERT_AFTER_FAILED_CYCLES + 1):
            await poller._track_health(*await poller.run_once())
        self.assertEqual(self.bot.sent, [])

    async def test_heartbeat_file_is_touched(self):
        poller = self.poller([domain(1, "a", -1)], {"a": [item(1)]})
        self.assertFalse(os.path.exists(self.heartbeat))
        await poller.run_once()
        self.assertTrue(os.path.exists(self.heartbeat))


if __name__ == "__main__":
    unittest.main()
