import unittest
from unittest.mock import AsyncMock, patch

import aiohttp

from app.vk import VkClient, VkError, VkUnavailable


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload


class FakeContext:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return FakeResponse(self._outcome)

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Отдаёт заранее заданные ответы по очереди: dict - JSON, исключение - сбой."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.requests = []

    def post(self, url, data=None):
        self.requests.append((url, dict(data or {})))
        return FakeContext(self._outcomes.pop(0))


def client(session, **kwargs):
    return VkClient(session, token="SECRET", version="5.199", request_delay=0, **kwargs)


def api_error(code, message="boom"):
    return {"error": {"error_code": code, "error_msg": message}}


class VkClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("app.vk.asyncio.sleep", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_get_wall_uses_vk_ru_and_sends_credentials_in_body(self):
        session = FakeSession({"response": {"count": 1, "items": [{"id": 1}]}})
        items = await client(session).get_wall("durov", 10)

        self.assertEqual(items, [{"id": 1}])
        url, data = session.requests[0]
        self.assertEqual(url, "https://api.vk.ru/method/wall.get")
        self.assertNotIn("SECRET", url)
        self.assertEqual(data["access_token"], "SECRET")
        self.assertEqual(data["v"], "5.199")
        self.assertEqual(data["domain"], "durov")
        self.assertEqual(data["count"], 10)

    async def test_custom_host(self):
        session = FakeSession({"response": {"items": []}})
        await VkClient(session, token="t", version="5.199", host="proxy.example", request_delay=0).get_wall("x", 1)
        self.assertEqual(session.requests[0][0], "https://proxy.example/method/wall.get")

    async def test_empty_wall(self):
        self.assertEqual(await client(FakeSession({"response": {"items": []}})).get_wall("x", 1), [])

    async def test_inaccessible_error_is_not_retried(self):
        session = FakeSession(api_error(15, "Access denied"))
        with self.assertRaises(VkError) as ctx:
            await client(session).get_wall("closed", 5)
        self.assertEqual(ctx.exception.code, 15)
        self.assertTrue(ctx.exception.inaccessible)
        self.assertEqual(len(session.requests), 1)

    async def test_other_errors_are_not_marked_inaccessible(self):
        with self.assertRaises(VkError) as ctx:
            await client(FakeSession(api_error(5, "auth failed"))).get_wall("x", 5)
        self.assertFalse(ctx.exception.inaccessible)

    async def test_rate_limit_is_retried(self):
        session = FakeSession(api_error(6), {"response": {"items": [{"id": 2}]}})
        self.assertEqual(await client(session).get_wall("x", 5), [{"id": 2}])
        self.assertEqual(len(session.requests), 2)

    async def test_rate_limit_gives_up_after_max_attempts(self):
        session = FakeSession(api_error(6), api_error(6), api_error(6))
        with self.assertRaises(VkError):
            await client(session).get_wall("x", 5)
        self.assertEqual(len(session.requests), 3)

    async def test_network_error_is_retried(self):
        session = FakeSession(aiohttp.ClientError("reset"), {"response": {"items": [{"id": 3}]}})
        self.assertEqual(await client(session).get_wall("x", 5), [{"id": 3}])

    async def test_network_error_eventually_raises_unavailable(self):
        session = FakeSession(*[aiohttp.ClientError("down")] * 3)
        with self.assertRaises(VkUnavailable):
            await client(session).get_wall("x", 5)
        self.assertEqual(len(session.requests), 3)

    async def test_non_json_body_is_unavailable(self):
        with self.assertRaises(VkUnavailable):
            await client(FakeSession(["not", "a", "dict"])).get_wall("x", 5)

    async def test_name_from_group(self):
        session = FakeSession({"response": {"groups": [{"id": 1, "name": "Моя группа"}]}})
        self.assertEqual(await client(session).get_name("g"), "Моя группа")
        self.assertEqual(session.requests[0][1]["group_id"], "g")

    async def test_name_from_group_old_response_format(self):
        session = FakeSession({"response": [{"id": 1, "name": "Старый формат"}]})
        self.assertEqual(await client(session).get_name("g"), "Старый формат")

    async def test_name_falls_back_to_user(self):
        session = FakeSession(
            api_error(100, "group_ids is invalid"),
            {"response": [{"id": 5, "first_name": "Иван", "last_name": "Петров"}]},
        )
        self.assertEqual(await client(session).get_name("ivan"), "Иван Петров")
        self.assertEqual(session.requests[1][0], "https://api.vk.ru/method/users.get")

    async def test_name_unknown(self):
        session = FakeSession(api_error(100), api_error(113))
        self.assertIsNone(await client(session).get_name("nobody"))


if __name__ == "__main__":
    unittest.main()
