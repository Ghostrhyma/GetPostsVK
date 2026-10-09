import unittest
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from app import texts
from app.posts import Media, VkPost
from app.schemas import ChatRef
from app.sender import deliver_post, is_dead_chat_error

CHAT = ChatRef(db_id=1, chat_id=-1001, thread_id=7)
TITLE = "Группа"


class FakeBot:
    def __init__(self, **failures):
        self.calls = []
        self.failures = {name: list(excs) for name, excs in failures.items()}

    async def _record(self, name, kwargs):
        self.calls.append((name, kwargs))
        queue = self.failures.get(name)
        if queue:
            raise queue.pop(0)

    async def send_message(self, **kwargs):
        await self._record("send_message", kwargs)

    async def send_photo(self, **kwargs):
        await self._record("send_photo", kwargs)

    async def send_media_group(self, **kwargs):
        await self._record("send_media_group", kwargs)

    def names(self):
        return [name for name, _ in self.calls]


def post(text="", *attachments):
    return VkPost(-100, 1, 1, text, tuple(attachments))


def photos(n):
    return [Media("photo", f"https://img/{i}.jpg") for i in range(n)]


DOC = Media("doc", "https://vk.ru/doc1", title="report.pdf")
VIDEO = Media("video", "https://vk.ru/video-1_2", preview="https://img/preview.jpg")
VIDEO_NO_PREVIEW = Media("video", "https://vk.ru/video-1_3")


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class DeliverPostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch("app.sender.asyncio.sleep", new=AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_text_only(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Привет"))
        self.assertEqual(bot.names(), ["send_message"])
        kwargs = bot.calls[0][1]
        self.assertEqual(kwargs["text"], "Группа\n\nПривет")
        self.assertEqual(kwargs["chat_id"], -1001)
        self.assertEqual(kwargs["message_thread_id"], 7)
        self.assertIsNone(kwargs["reply_markup"])

    async def test_empty_text_sends_only_title(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post(""))
        self.assertEqual(bot.calls[0][1]["text"], "Группа")

    async def test_very_long_text_is_split_and_buttons_go_last(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("слово " * 1000, DOC))  # ~6000 символов
        self.assertEqual(bot.names(), ["send_message", "send_message"])
        first, second = (c[1] for c in bot.calls)
        self.assertTrue(len(first["text"]) <= 4096 and len(second["text"]) <= 4096)
        self.assertIsNone(first["reply_markup"])
        self.assertEqual(buttons(second["reply_markup"])[0].url, DOC.url)

    async def test_single_photo_with_caption_and_buttons_in_one_message(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Текст", photos(1)[0], DOC))
        self.assertEqual(bot.names(), ["send_photo"])
        kwargs = bot.calls[0][1]
        self.assertEqual(kwargs["photo"], "https://img/0.jpg")
        self.assertEqual(kwargs["caption"], "Группа\n\nТекст")
        self.assertEqual(buttons(kwargs["reply_markup"])[0].text, "Файл из поста - report.pdf")

    async def test_single_photo_with_long_text(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("а" * 1500, photos(1)[0], DOC))
        self.assertEqual(bot.names(), ["send_photo", "send_message"])
        photo_call, text_call = (c[1] for c in bot.calls)
        self.assertIsNone(photo_call["caption"])
        self.assertIsNone(photo_call["reply_markup"])
        self.assertIn("а" * 1500, text_call["text"])
        self.assertIsNotNone(text_call["reply_markup"])

    async def test_album_caption_only_on_first_item(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Текст", *photos(3)))
        self.assertEqual(bot.names(), ["send_media_group"])
        media = bot.calls[0][1]["media"]
        self.assertEqual([m.caption for m in media], ["Группа\n\nТекст", None, None])

    async def test_album_with_buttons_gets_followup_message(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Текст", *photos(2), VIDEO))
        self.assertEqual(bot.names(), ["send_media_group", "send_message"])
        followup = bot.calls[1][1]
        self.assertEqual(followup["text"], texts.ATTACHMENTS)
        self.assertEqual(buttons(followup["reply_markup"])[0].text, "Видео из поста")
        self.assertEqual(buttons(followup["reply_markup"])[0].url, VIDEO.url)

    async def test_album_with_long_text(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("а" * 2000, *photos(2), DOC))
        self.assertEqual(bot.names(), ["send_media_group", "send_message"])
        media = bot.calls[0][1]["media"]
        self.assertTrue(all(m.caption is None for m in media))
        self.assertIsNotNone(bot.calls[1][1]["reply_markup"])

    async def test_eleven_photos_are_split_without_a_lonely_item(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Текст", *photos(11)))
        self.assertEqual(bot.names(), ["send_media_group", "send_media_group"])
        first, second = (c[1]["media"] for c in bot.calls)
        self.assertEqual((len(first), len(second)), (9, 2))
        self.assertIsNotNone(first[0].caption)
        self.assertIsNone(second[0].caption)

    async def test_video_preview_is_sent_as_photo_with_button(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Смотрите", VIDEO))
        self.assertEqual(bot.names(), ["send_photo"])
        kwargs = bot.calls[0][1]
        self.assertEqual(kwargs["photo"], VIDEO.preview)
        self.assertEqual(buttons(kwargs["reply_markup"])[0].url, VIDEO.url)

    async def test_video_without_preview_falls_back_to_text_with_button(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("Смотрите", VIDEO_NO_PREVIEW))
        self.assertEqual(bot.names(), ["send_message"])
        self.assertEqual(buttons(bot.calls[0][1]["reply_markup"])[0].url, VIDEO_NO_PREVIEW.url)

    async def test_several_videos_are_numbered(self):
        bot = FakeBot()
        await deliver_post(bot, CHAT, TITLE, post("x", VIDEO_NO_PREVIEW, VIDEO))
        labels = [b.text for b in buttons(bot.calls[-1][1]["reply_markup"])]
        self.assertEqual(labels, ["Видео из поста 1", "Видео из поста 2"])

    async def test_failed_photo_falls_back_to_text(self):
        bad = TelegramBadRequest(method=None, message="Bad Request: failed to get HTTP URL content")
        bot = FakeBot(send_photo=[bad])
        await deliver_post(bot, CHAT, TITLE, post("Текст", photos(1)[0], DOC))
        self.assertEqual(bot.names(), ["send_photo", "send_message"])
        fallback = bot.calls[1][1]
        self.assertEqual(fallback["text"], "Группа\n\nТекст")
        self.assertIsNotNone(fallback["reply_markup"])

    async def test_dead_chat_error_is_not_swallowed(self):
        gone = TelegramBadRequest(method=None, message="Bad Request: chat not found")
        bot = FakeBot(send_photo=[gone])
        with self.assertRaises(TelegramBadRequest):
            await deliver_post(bot, CHAT, TITLE, post("Текст", photos(1)[0]))
        self.assertEqual(bot.names(), ["send_photo"])

    async def test_forbidden_propagates(self):
        kicked = TelegramForbiddenError(method=None, message="Forbidden: bot was kicked from the group chat")
        with self.assertRaises(TelegramForbiddenError):
            await deliver_post(FakeBot(send_message=[kicked]), CHAT, TITLE, post("Текст"))

    async def test_flood_limit_is_retried(self):
        flood = TelegramRetryAfter(method=None, message="Too Many Requests", retry_after=2)
        bot = FakeBot(send_message=[flood])
        await deliver_post(bot, CHAT, TITLE, post("Текст"))
        self.assertEqual(bot.names(), ["send_message", "send_message"])

    async def test_network_error_is_retried_then_raised(self):
        net = lambda: TelegramNetworkError(method=None, message="timeout")  # noqa: E731
        bot = FakeBot(send_message=[net()])
        await deliver_post(bot, CHAT, TITLE, post("Текст"))
        self.assertEqual(len(bot.calls), 2)

        bot = FakeBot(send_message=[net(), net(), net()])
        with self.assertRaises(TelegramNetworkError):
            await deliver_post(bot, CHAT, TITLE, post("Текст"))
        self.assertEqual(len(bot.calls), 3)


class DeadChatDetection(unittest.TestCase):
    def test_classification(self):
        forbidden = TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")
        self.assertTrue(is_dead_chat_error(forbidden))
        for message, expected in (
            ("Bad Request: chat not found", True),
            ("Bad Request: message thread not found", True),
            ("Bad Request: TOPIC_DELETED", True),
            ("Bad Request: wrong file identifier/HTTP URL specified", False),
            ("Bad Request: message is too long", False),
        ):
            with self.subTest(message=message):
                self.assertEqual(
                    is_dead_chat_error(TelegramBadRequest(method=None, message=message)), expected
                )
        self.assertFalse(is_dead_chat_error(RuntimeError("x")))


if __name__ == "__main__":
    unittest.main()
