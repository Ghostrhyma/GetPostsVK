import unittest

from app.posts import (
    Media,
    VkPost,
    chunk_album,
    extract_domain,
    parse_post,
    split_new_posts,
    split_text,
    video_url,
)


def photo(*sizes):
    return {"type": "photo", "photo": {"sizes": [
        {"type": t, "url": u, "width": w, "height": h} for t, u, w, h in sizes
    ]}}


class ExtractDomain(unittest.TestCase):
    def test_accepted(self):
        cases = {
            "https://vk.ru/ghostrhyme": "ghostrhyme",
            "http://vk.ru/Ghostrhyme/": "ghostrhyme",
            "https://m.vk.ru/club123?w=wall-1_2": "club123",
            "https://vk.com/some_group": "some_group",
            "vk.ru/durov": "durov",
            "https://vk.ru/public210109455#top": "public210109455",
            "посмотри https://vk.ru/russian_humor пожалуйста": "russian_humor",
            "https://vk.ru/durov.": "durov",
            "(https://vk.ru/durov)": "durov",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_domain(text), expected)

    def test_rejected(self):
        for text in (
            None,
            "",
            "просто текст",
            "https://vk.ru/wall-1_2",
            "https://vk.ru/feed",
            "https://vk.ru/",
            "https://example.com/vk.ru/x",
            "https://notvk.ru/x",
            "https://t.me/channel",
        ):
            with self.subTest(text=text):
                self.assertIsNone(extract_domain(text))


class ParsePost(unittest.TestCase):
    def test_basic_text_post(self):
        post = parse_post({"id": 7, "owner_id": -100, "date": 5, "text": " Привет "})
        self.assertEqual(post, VkPost(-100, 7, 5, "Привет", ()))
        self.assertEqual(post.key, "-100-7")

    def test_broken_items_are_skipped(self):
        for item in (None, {}, {"id": 1}, {"id": "x", "owner_id": 1}, "str"):
            with self.subTest(item=item):
                self.assertIsNone(parse_post(item))

    def test_photo_size_selection(self):
        item = {"id": 1, "owner_id": -1, "attachments": [photo(
            ("s", "small", 75, 56), ("x", "mid", 604, 453), ("y", "big", 1080, 810), ("w", "huge", 2560, 1920),
        )]}
        self.assertEqual(parse_post(item).attachments, (Media("photo", "big"),))

    def test_photo_when_everything_is_too_large_takes_smallest(self):
        item = {"id": 1, "owner_id": -1, "attachments": [photo(("w", "a", 3000, 2000), ("z", "b", 2000, 1500))]}
        self.assertEqual(parse_post(item).attachments, (Media("photo", "b"),))

    def test_doc_and_video(self):
        item = {"id": 1, "owner_id": -1, "attachments": [
            {"type": "doc", "doc": {"url": "https://vk.ru/doc1", "title": "report.pdf"}},
            {"type": "video", "video": {
                "owner_id": -167, "id": 456, "access_key": "abc", "title": "Клип",
                "image": [
                    {"url": "p130", "width": 130, "height": 97},
                    {"url": "p640", "width": 640, "height": 480},
                    {"url": "p1280", "width": 1280, "height": 720},
                ],
            }},
        ]}
        doc, video = parse_post(item).attachments
        self.assertEqual(doc, Media("doc", "https://vk.ru/doc1", title="report.pdf"))
        self.assertEqual(video.kind, "video")
        self.assertEqual(video.url, "https://vk.ru/video-167_456?access_key=abc")
        self.assertEqual(video.preview, "p640")

    def test_video_without_access_key_or_preview(self):
        item = {"id": 1, "owner_id": -1, "attachments": [
            {"type": "video", "video": {"owner_id": 5, "id": 6}}
        ]}
        video = parse_post(item).attachments[0]
        self.assertEqual(video.url, "https://vk.ru/video5_6")
        self.assertIsNone(video.preview)

    def test_unknown_and_broken_attachments_are_ignored(self):
        item = {"id": 1, "owner_id": -1, "attachments": [
            {"type": "poll", "poll": {"id": 1}},
            {"type": "photo", "photo": {"sizes": []}},
            {"type": "doc", "doc": {}},
            {"type": "video", "video": {"id": 1}},
            None,
            {"type": "photo"},
        ]}
        self.assertEqual(parse_post(item).attachments, ())

    def test_repost_merges_comment_and_original(self):
        item = {
            "id": 10, "owner_id": -1, "text": "Смотрите!",
            "attachments": [photo(("x", "own", 600, 400))],
            "copy_history": [{
                "id": 99, "owner_id": -2, "text": "Оригинал",
                "attachments": [photo(("x", "orig", 600, 400))],
            }],
        }
        post = parse_post(item)
        self.assertEqual(post.text, "Смотрите!\n\nОригинал")
        self.assertEqual([m.url for m in post.attachments], ["own", "orig"])
        self.assertEqual(post.key, "-1-10")  # ключ по самой записи на стене, не по оригиналу

    def test_repost_without_comment(self):
        item = {"id": 10, "owner_id": -1, "text": "", "copy_history": [{"id": 99, "owner_id": -2, "text": "Оригинал"}]}
        self.assertEqual(parse_post(item).text, "Оригинал")

    def test_video_url_helper_uses_vk_ru(self):
        self.assertTrue(video_url(1, 2).startswith("https://vk.ru/"))


def mk(post_id, date=None, owner=-100):
    return VkPost(owner, post_id, date if date is not None else post_id, f"t{post_id}")


class SplitNewPosts(unittest.TestCase):
    def test_first_sight_sends_only_the_newest(self):
        posts = [mk(3), mk(1), mk(2)]
        new, to_send = split_new_posts(posts, [])
        self.assertEqual([p.post_id for p in new], [1, 2, 3])
        self.assertEqual([p.post_id for p in to_send], [3])

    def test_nothing_new(self):
        new, to_send = split_new_posts([mk(1), mk(2)], ["-100-2", "-100-1"])
        self.assertEqual((new, to_send), ([], []))

    def test_new_posts_are_sent_oldest_first(self):
        new, to_send = split_new_posts([mk(5), mk(4), mk(3)], ["-100-3"])
        self.assertEqual([p.post_id for p in to_send], [4, 5])
        self.assertEqual([p.post_id for p in new], [4, 5])

    def test_upgrade_from_old_bot_does_not_flood(self):
        # Старая версия помнила только последний пост (id 50), на стене - 10 постов
        posts = [mk(i) for i in range(41, 51)] + [mk(51)]
        new, to_send = split_new_posts(posts, ["-100-50"])
        self.assertEqual([p.post_id for p in to_send], [51])
        self.assertEqual(len(new), 10)  # старые запоминаются, но не отправляются

    def test_old_pinned_post_is_remembered_but_not_sent(self):
        pinned = mk(3, date=1)  # старый закреплённый пост, которого нет в БД
        new, to_send = split_new_posts([pinned, mk(10), mk(9)], ["-100-10", "-100-9"])
        self.assertEqual([p.post_id for p in new], [3])
        self.assertEqual(to_send, [])

    def test_legacy_repost_keys_of_other_owners_are_ignored(self):
        # В БД старой версии у репоста был ключ оригинала (чужой owner, огромный id)
        recent = ["-999-9000000", "-100-20"]
        _, to_send = split_new_posts([mk(21), mk(20)], recent)
        self.assertEqual([p.post_id for p in to_send], [21])

    def test_only_foreign_keys_known_counts_as_first_sight(self):
        recent = ["-999-9000000"]
        _, to_send = split_new_posts([mk(3), mk(2), mk(1)], recent)
        self.assertEqual([p.post_id for p in to_send], [3])

    def test_garbage_keys_do_not_break(self):
        _, to_send = split_new_posts([mk(2), mk(1)], ["garbage", "a-b", "-100-1"])
        self.assertEqual([p.post_id for p in to_send], [2])


class TextHelpers(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(split_text("привет", 4096), ["привет"])

    def test_long_text_splits_on_paragraphs(self):
        text = ("a" * 60 + "\n\n") * 5
        chunks = split_text(text, 130)
        self.assertTrue(all(len(c) <= 130 for c in chunks))
        self.assertEqual("".join(chunks).replace("\n", ""), "a" * 300)

    def test_long_text_without_separators_hard_cut(self):
        chunks = split_text("x" * 250, 100)
        self.assertEqual([len(c) for c in chunks], [100, 100, 50])

    def test_telegram_limit_roundtrip(self):
        text = " ".join(["слово"] * 3000)
        chunks = split_text(text, 4096)
        self.assertTrue(all(len(c) <= 4096 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_album_chunks(self):
        self.assertEqual([len(c) for c in chunk_album(list(range(3)))], [3])
        self.assertEqual([len(c) for c in chunk_album(list(range(10)))], [10])
        self.assertEqual([len(c) for c in chunk_album(list(range(11)))], [9, 2])
        self.assertEqual([len(c) for c in chunk_album(list(range(21)))], [10, 9, 2])
        self.assertEqual([len(c) for c in chunk_album([1])], [1])


if __name__ == "__main__":
    unittest.main()
